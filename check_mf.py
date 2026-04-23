from flask import Flask, render_template, request, jsonify
import base64
from gemini_utils import audit_face_violation # Ensure this is imported correctly
import os
from datetime import datetime
app = Flask(__name__)

@app.route('/')
def index():
    return render_template('test_web.html')

@app.route('/test_audit', methods=['POST'])
def test_audit():
    data = request.get_json()
    image_data = data.get('image').split(",")[1]
    
    # --- DEBUG: Save the image to see what the AI sees ---
    if not os.path.exists("debug_images"):
        os.makedirs("debug_images")
    
    with open(f"debug_images/test_{datetime.now().strftime('%H%M%S')}.jpg", "wb") as f:
        f.write(base64.b64decode(image_data))
    # ----------------------------------------------------

    verdict = audit_face_violation(image_data)
    return jsonify({"verdict": verdict})

if __name__ == '__main__':
    app.run(debug=True, port=5001) # Running on 5001 to avoid conflict with main app