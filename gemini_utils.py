import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types 
import base64
from PIL import Image
import io

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
PRO_MODEL = "models/gemini-2.5-pro"
FLASH_MODEL = "models/gemini-2.5-flash"


import time
import json

def generate_questions(jd, resume):
    # Defining the model outside the retry loop
    # Switching to FLASH_MODEL is good as it's faster and more available
    
    prompt = f"""
    You are an entry-level technical interviewer. 
    Based on the JD and Resume provided, generate:
    - 2 EASY technical questions
    - 1 EASY behavioral question
    - 2 EASY project-based questions
    
    Format the output as a JSON object with keys: 
    'technical_questions', 'behavioral_questions', 'project_questions'.
    
    Job Description: {jd}
    Resume: {resume}
    """

    # Retry logic to handle the 503 "High Demand" error
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Use 'response_mime_type' to force Gemini to return valid JSON
            response = client.models.generate_content(
                model=FLASH_MODEL,
                contents=prompt,
                config={
                    'response_mime_type': 'application/json'
                }
            )

            raw_text = response.text.strip()

            # Safety: Remove potential markdown artifacts
            if raw_text.startswith("```"):
                raw_text = raw_text.replace("```json", "").replace("```", "").strip()

            return json.loads(raw_text)

        except Exception as e:
            # If it's a 503 error, wait and try again
            if "503" in str(e) or "demand" in str(e):
                print(f"⚠️ Gemini busy (Attempt {attempt + 1}/{max_retries}). Retrying in 2s...")
                time.sleep(2)
                continue
            else:
                print(f"❌ Gemini Error: {e}")
                break

    # Fallback if all retries fail
    return {
        "technical_questions": ["Could you explain a technical challenge you faced recently?"],
        "behavioral_questions": ["Why are you interested in this role?"],
        "project_questions": ["Tell me about the most recent project you worked on."]
    }


# import google.generativeai as genai
from PIL import Image
import io
import base64

def audit_face_violation(image_b64):
    """ Expert AI Judge using the NEW SDK syntax """
    try:
        # Decode image
        image_data = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(image_data))
        
        prompt = """
        STRICT PROCTORING AUDIT. sit against a PLAIN, EMPTY BACKGROUND.
        Return ONLY 'TERMINATE' if there are multiple people or unauthorized devices.
        Otherwise return 'STAY'.
        """

        # Correct call for the NEW SDK
        response = client.models.generate_content(
            model=FLASH_MODEL, # Flash is actually very good at image detection now
            contents=[prompt, img]
        )

        verdict = response.text.strip().upper()
        return "TERMINATE" if "TERMINATE" in verdict else "STAY"

    except Exception as e:
        print(f"⚠️ Audit Error: {e}")
        return "STAY"


import time
import json

def evaluate_performance(jd, answers, flags):
    """ Combined Evaluation: Text + Integrity check with Retry Logic. """
    formatted_answers = ""
    for item in answers:
        formatted_answers += f"Q: {item['question']}\nA: {item['answer']}\n\n"
    
    flag_summary = ", ".join([f"{f['violation']} ({f.get('details', '')})" for f in flags]) if flags else "No violations detected."

    prompt = f"""
    Evaluate this candidate fairly. Use MAX 15 words per bullet point.
    CRITICAL INSTRUCTION: 
    - If the transcript contains mostly "Listening..." or non-answers, the Technical Score MUST be 0.
    - If the candidate speaks about the system/rules instead of answering, flag this as 'Low Engagement' in weaknesses.
    - If there are NO technical answers, do not hallucinate strengths.
    
    JD: {jd}
    Transcript: {formatted_answers}
    Proctoring Flags: {flag_summary}

    Return JSON:
    {{
        "technical_score": 0-10,
        "communication_score": 0-10,
        "integrity_score": 0-10,
        "strengths": ["string"],
        "weaknesses": ["string"],
        "overall_recommendation": "Short 2-sentence summary."
    }}
    """

    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Force JSON mode for better parsing
            response = client.models.generate_content(
                model=FLASH_MODEL, 
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            
            raw_text = response.text.strip()
            
            # Basic cleanup in case Gemini ignores the config
            if raw_text.startswith("```"):
                raw_text = raw_text.replace("```json", "").replace("```", "").strip()
            
            data = json.loads(raw_text)
            
            # Post-processing for professional reports
            if not data.get("strengths"): data["strengths"] = ["Consistent technical answers"]
            if not data.get("weaknesses"): data["weaknesses"] = ["No significant weaknesses noted"]
            
            return data

        except Exception as e:
            if "503" in str(e) or "demand" in str(e):
                print(f"⚠️ Gemini busy (Evaluation). Attempt {attempt+1}/{max_retries}. Retrying in 3s...")
                time.sleep(3)
                continue
            else:
                print(f"❌ AI Error during evaluation: {e}")
                break

    # Fallback return if all retries fail
    return {
        "technical_score": 0,
        "communication_score": 0,
        "integrity_score": 5, 
        "strengths": ["System error during evaluation"],
        "weaknesses": ["Service unavailable"],
        "overall_recommendation": "AI Service is currently experiencing high demand. Please try running the evaluation again from the dashboard in a few minutes."
    }