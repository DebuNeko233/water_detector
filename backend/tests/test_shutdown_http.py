"""Only the Electron-owned process can request Python shutdown."""
import threading
from fastapi.testclient import TestClient
from water_detector.service.shutdown import ShutdownController, is_authorized
from water_detector.service.api import create_app
from water_detector.config.manager import ConfigManager

def test_shutdown_authorization():
    assert is_authorized("127.0.0.1","secret","secret")
    assert is_authorized("::1","secret","secret")
    assert not is_authorized("192.168.1.4","secret","secret")
    assert not is_authorized("testclient","secret","secret")
    assert not is_authorized("127.0.0.1","incorrect","secret")
    assert not is_authorized("127.0.0.1","secret","")

def test_shutdown_http(tmp_path):
    stopped=threading.Event()
    controller=ShutdownController("runtime-secret")
    controller.register(stopped.set)
    with TestClient(create_app(ConfigManager(tmp_path),controller),client=("127.0.0.1",4567)) as client:
        assert client.post("/api/system/process/shutdown").status_code == 403
        assert client.post("/api/system/process/shutdown",headers={"X-Wafer-Control-Token":"bad"}).status_code == 403
        assert client.post("/api/system/process/shutdown",headers={"X-Wafer-Control-Token":"runtime-secret"}).status_code == 202
        assert stopped.wait(2)
        assert client.post("/api/system/process/shutdown",headers={"X-Wafer-Control-Token":"runtime-secret"}).status_code == 202
