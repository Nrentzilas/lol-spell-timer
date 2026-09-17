from __future__ import annotations
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

PROTOCOL_VERSION = 1
TOPIC_TEMPLATE = "loltimer/v1/{room}"

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


class SyncClient:
    def __init__(self, room: str, broker: str, port: int = 1883):
        self.room = sanitize_room(room)
        self.broker = broker
        self.port = port
        self.client_id = uuid.uuid4().hex[:8]
        self.topic = TOPIC_TEMPLATE.format(room=self.room) if self.room else ""
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

            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message
            self._client.reconnect_delay_set(min_delay=1, max_delay=30)
            self._client.connect_async(self.broker, self.port, keepalive=30)
            self._client.loop_start()
            log.info(f"Connecting to {self.broker}:{self.port} room '{self.room}'...")
        except Exception as e:
            log.warning(f"Startup failed: {e}")
            self._client = None

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
            log.warning(f"Connect refused (rc={rc})")
            return
        self.connected = True
        client.subscribe(self.topic, qos=0)
        log.info(f"Connected. Listening on '{self.topic}'")
        self.publish(MSG_HELLO, {})

    def _on_disconnect(self, client, userdata, rc, *args):
        self.connected = False
        log.warning("Disconnected (will retry).")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
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
            log.warning(f"Publish failed: {e}")

    def poll(self) -> List[Dict[str, Any]]:
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                break
        return out
