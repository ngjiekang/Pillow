from PIL import Image
from PIL import ImageFile

from flask import Flask, request
app = Flask(__name__)

@app.route("/files/<filename>")
def test_load_djpeg(self):
  img = Image.open(filename)
  img.load_djpeg()
  eval(filename)
