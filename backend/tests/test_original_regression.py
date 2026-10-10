"""Compare every frame to user's unchanged main.py; set SOURCE_MAIN, GOOD_VIDEO/BAD_VIDEO in CI."""
import importlib.util
import os
from pathlib import Path
import pytest
import cv2
from water_detector.detector.water import WaterDetector
from water_detector.config.schema import DetectorConfig, Calibration

ORIGINAL = Path(os.environ.get('SOURCE_MAIN', str(Path(__file__).resolve().parents[2] / 'legacy' / 'main.py')))


@pytest.mark.parametrize('name,cx,cy,r', [('good',255,401,150),('bad',255,477,148)])
def test_exact_water_score_against_original(name,cx,cy,r):
    video = Path(os.environ.get(name.upper()+'_VIDEO',f'/mnt/data/{name}.mp4'))
    if not ORIGINAL.is_file() or not video.is_file():
        pytest.skip('Original source and test videos not installed')
    spec=importlib.util.spec_from_file_location('original_main',ORIGINAL)
    original=importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name]=original
    spec.loader.exec_module(original)
    cap=cv2.VideoCapture(str(video))
    fps=cap.get(cv2.CAP_PROP_FPS)
    ref=original.WaterDetector((576,720),fps,original.Calibration(cx,cy,r),original.DemoConfig())
    new=WaterDetector((576,720),fps,Calibration(cx=cx,cy=cy,radius=r,reference_width=720,reference_height=576),DetectorConfig())
    count=0
    while True:
        ok,frame=cap.read()
        if not ok:break
        a,b=ref.process(frame),new.process(frame)
        assert a['status']==b['status'],f'{name} frame {count}'
        assert a['s_water']==b['s_water'],f'{name} frame {count}'
        assert a['sector_scores']==b['sector_scores'],f'{name} frame {count}'
        count+=1
    cap.release()
    assert count > 100
