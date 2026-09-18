"""Duo sync over MQTT: two people sharing one set of timers.

The room code never travels and is never the topic. People pick codes like
"duo", and on a public broker that means sharing a channel with strangers, so
the topic is a hash of the code instead. It is still not a password -- anyone
who guesses the code derives the same topic -- but it stops the collisions
that made short codes leak into each other.
"""

from __future__ import annotations
import hashlib
import json
import logging
import queue
import re
import threading
import uuid
from typing import Any, Dict, List, Optional

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False

log = logging.getLogger("sync")

# v2: topics are hashed and `start` carries the base cooldown and haste, so a
# Cosmic Insight flag can still correct a timer that arrived from a partner.
PROTOCOL_VERSION = 2
TOPIC_TEMPLATE = "loltimer/v2/{room}"
TOPIC_SALT = "loltimer-room:"

MSG_START = "start"
MSG_RESET = "reset"
MSG_HELLO = "hello"
MSG_STATE = "state"
MSG_CI = "ci"
MSG_ADJUST = "adjust"


def sanitize_room(room: Optional[str]) -> str:
    if not room:
        return ""
    return re.sub(r'[^A-Za-z0-9_-]', '', room.strip())[:32]


def room_topic(room: Optional[str]) -> str:
    """The MQTT topic a room code maps to, or '' for no room."""
    clean = sanitize_room(room)
    if not clean:
        return ""
    digest = hashlib.sha256((TOPIC_SALT + clean).encode("utf-8")).hexdigest()
    return TOPIC_TEMPLATE.format(room=digest[:24])


class SyncClient:
    def __init__(self, room: str, broker: str, port: int = 1883,
                 tls: bool = False):
        self.room = sanitize_room(room)
        self.broker = broker
        self.port = port
        self.tls = bool(tls)
        self.client_id = uuid.uuid4().hex[:8]
        self.topic = room_topic(self.room)
        self.inbox: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.connected = False
        self.enabled = bool(self.room) and HAS_MQTT
        self._client = None
        self._lock = threading.Lock()

    def start(self):
        if not self.enabled:
            if self.room and not HAS_MQTT:
                log.warning("paho-mqtt not installed -- sync disabled. Run: pip install paho-mqtt")
            return
        try:
            if hasattr(mqtt, "CallbackAPIVersion"):
                self._client = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                    client_id=f"loltimer-{self.client_id}",
                )
            else:
                self._client = mqtt.Client(client_id=f"loltimer-{self.client_id}")

            if self.tls:
                # Default context: the system trust store, hostname checked.
                self._client.tls_set()

            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message
            self._client.reconnect_delay_set(min_delay=1, max_delay=30)
            self._client.connect_async(self.broker, self.port, keepalive=30)
            self._client.loop_start()
            log.info("Connecting to %s:%d%s, room '%s'...", self.broker,
                     self.port, " over TLS" if self.tls else "", self.room)
        except Exception as e:
            log.warning("Startup failed: %s", e)
            self._client = None
            self.enabled = False

    def stop(self):
        if not self._client:
            return
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass
        self._client = None
        self.connected = False

    def _on_connect(self, client, userdata, flags, rc, *args):
        if rc != 0:
            log.warning("Connect refused (rc=%s)", rc)
            return
        self.connected = True
        client.subscribe(self.topic, qos=0)
        log.info("Connected, room '%s' is live.", self.room)
        self.publish(MSG_HELLO, {})

    def _on_disconnect(self, client, userdata, rc, *args):
        self.connected = False
        if rc == 0:
            log.info("Disconnected.")
            return
        # Without the reason, a broker that refuses us reads like a code bug.
        log.warning("Disconnected from %s: %s (will retry).", self.broker,
                    mqtt.error_string(rc) if isinstance(rc, int) else rc)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        if payload.get("v") != PROTOCOL_VERSION:
            return
        if payload.get("src") == self.client_id:
            return
        self.inbox.put(payload)

    def publish(self, msg_type: str, body: Dict[str, Any]):
        if not self._client:
            return
        payload = {"v": PROTOCOL_VERSION, "type": msg_type, "src": self.client_id}
        payload.update(body)
        try:
            with self._lock:
                self._client.publish(self.topic, json.dumps(payload), qos=0)
        except Exception as e:
            log.warning("Publish failed: %s", e)

    def poll(self) -> List[Dict[str, Any]]:
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                break
        return out
