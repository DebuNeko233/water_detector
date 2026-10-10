from pathlib import Path
import tempfile
import time
from fastapi.testclient import TestClient
from water_detector.config.manager import ConfigManager
from water_detector.service.api import create_app
from water_detector.config.schema import AppConfig, Calibration


def test_config_and_health():
    with tempfile.TemporaryDirectory() as tmp:
        mgr=ConfigManager(Path(tmp))
        with TestClient(create_app(mgr)) as cli:
            assert cli.get('/api/health').json()['ok']
            cfg=cli.get('/api/config/active').json()
            assert cfg['detector']['s_water_threshold']==.010
            cfg['calibrations']['P1']={'cx':255,'cy':401,'radius':150,'reference_width':720,'reference_height':576}
            r=cli.post('/api/config/commit',json=cfg)
            assert r.status_code==200
            assert r.json()['version']==2
            assert cli.get('/api/config/active').json()['calibrations']['P1']['radius']==150


def test_debug_preview_and_frame():
    video=Path('/mnt/data/good.mp4')
    if not video.exists():return
    with tempfile.TemporaryDirectory() as tmp:
        with TestClient(create_app(ConfigManager(Path(tmp)))) as cli:
            source={'kind':'file','address':str(video),'device_index':0}
            frame=cli.post('/api/camera/frame',json={'source':source,'frame_index':2})
            assert frame.status_code==200 and frame.json()['width']==720
            cfg=AppConfig()
            c=Calibration(cx=255,cy=401,radius=150,reference_width=720,reference_height=576)
            r=cli.post('/api/debug/preview',json={'source':source,'frame_index':50,'calibration':c.model_dump(),'detector':cfg.detector.model_dump(),'stage':'P1'})
            assert r.status_code==200 and r.json()['samples']
