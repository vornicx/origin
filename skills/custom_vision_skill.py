import cv2, numpy, pytesseract, mss, time
from PIL import Image

class CustomVisionSkill:
    def __init__(self):
        self.sct = mss.mss()
    
    def capture_and_analyze(self, question=''):
        screenshot = self.sct.grab(self.sct.monitors[1])
        img = Image.frombytes('RGB', screenshot.size, screenshot.rgb)
        img_cv = cv2.cvtColor(numpy.array(img), cv2.COLOR_RGB2BGR)
        text = pytesseract.image_to_string(img_cv, lang='spa+eng')
        return {'text': text, 'image': img_cv, 'question': question}
    
    def find_elements(self, description):
        return {'elements': ['button', 'text_field', 'link'], 'confidence': 0.95}
    
    def read_text_region(self, x, y, w, h):
        return 'texto de ejemplo en la región'