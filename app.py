
from flask import Flask, render_template, request, url_for, jsonify, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, login_user, LoginManager, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from gemini_utils import generate_questions, evaluate_performance, audit_face_violation
from resume_parser import extract_text_from_pdf
import uuid
import json
import os
from datetime import datetime
import base64

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'any_random_string_for_local_dev')

# --- DATABASE CONFIGURATION ---
# app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///interviews.db'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'interviews.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)



# --- AUTH CONFIGURATION ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Redirects here if not logged in

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- DATABASE MODEL ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    # Relationship: A user owns multiple interviews
    interviews = db.relationship('Interview', backref='recruiter', lazy=True)


class Interview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    proctoring_flags = db.Column(db.Text, default="[]") # Stores a list of violations as a string
    candidate_name = db.Column(db.String(100))
    jd_text = db.Column(db.Text, nullable=False)
    questions_json = db.Column(db.Text, nullable=False)  
    answers_json = db.Column(db.Text, nullable=True)    
    status = db.Column(db.String(20), default="Pending") 
    is_used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    evaluation_json = db.Column(db.Text, nullable=True)

# Initialize the database
with app.app_context():
    db.create_all()


@app.route("/signup", methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Check if user exists
        if User.query.filter_by(email=email).first():
            return "Email already exists", 400
            
        # Create user
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(name=name,email=email, password=hashed_pw)
        
        db.session.add(new_user)
        db.session.commit()
        
        # Log them in
        login_user(new_user)
        
        # IMPORTANT: Use a "next" param or direct redirect
        return redirect(url_for('recruiter_dashboard'))
        
    return render_template("signup.html")



@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password, password):
            # remember=False means the session cookie expires when the browser closes
            login_user(user, remember=False) 
            return redirect(url_for('recruiter_dashboard'))
        
        return "Invalid email or password", 401
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route("/")
def index():
    # If you want to FORCE the login page every time someone hits the base URL:
    return redirect(url_for('login'))


@app.route("/dashboard")
@login_required
def recruiter_dashboard():
    # Fetch all interviews, newest first
    all_interviews = Interview.query.filter_by(user_id=current_user.id).order_by(Interview.created_at.desc()).all()
    return render_template("dashboard.html", interviews=all_interviews)

@app.route("/cancel_interview/<session_id>", methods=["POST"])
@login_required
def cancel_interview(session_id):
    interview = Interview.query.filter_by(session_id=session_id).first()
    if interview.user_id != current_user.id:
        return {"status": "unauthorized"}, 403
    if not interview:
        return {"status": "error"}, 404

    data = request.get_json()
    reason = data.get("reason", "Proctoring Violation")

    # Update status to Cancelled
    interview.status = "Cancelled"
    
    # Optional: Log the final reason in the flags
    flags = json.loads(interview.proctoring_flags) if interview.proctoring_flags else []
    flags.append({
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "violation": "CRITICAL",
        "details": f"Interview Terminated: {reason}"
    })
    interview.proctoring_flags = json.dumps(flags)
    
    db.session.commit()
    return {"status": "success"}

@app.route("/create_interview", methods=["POST"])
@login_required
def create_interviews():
    jd = request.form["jd"]
    names = request.form.getlist("candidate_names[]")
    resume_files = request.files.getlist("resumes[]")

    # Zip names and files together to process them as pairs
    for name, resume_file in zip(names, resume_files):
        if name and resume_file and resume_file.filename != '':
            # 1. Process resume
            resume_text = extract_text_from_pdf(resume_file)
            questions = generate_questions(jd, resume_text)
            session_id = str(uuid.uuid4())

            # 2. Save record
            new_interview = Interview(
                session_id=session_id,
                user_id=current_user.id,
                candidate_name=name,
                jd_text=jd,
                questions_json=json.dumps(questions)
            )
            db.session.add(new_interview)
    
    db.session.commit()
    # Redirect back to the dashboard to see the new links
    return redirect(url_for('recruiter_dashboard'))

@app.route("/interview/<session_id>")
def start_interview(session_id):
    interview = Interview.query.filter_by(session_id=session_id).first()

    if not interview:
        return "Invalid or expired interview link", 404

    if interview.is_used and interview.status != "Pending":
        return """
        <div style="text-align:center; padding:50px; font-family:sans-serif;">
            <h2 style="color:red;">Link Expired</h2>
            <p>This interview link has already been used. You cannot restart or refresh the session.</p>
        </div>
        """, 403
    
    return render_template("guidelines.html", session_id=session_id, name=interview.candidate_name)

@app.route("/begin_session/<session_id>")
def begin_session(session_id):
    interview = Interview.query.filter_by(session_id=session_id).first()
    
    if not interview or interview.is_used:
        return redirect(url_for('start_interview', session_id=session_id))

    # Mark as used ONLY when they actually start the questions
    interview.is_used = True
    db.session.commit()
    
    questions = json.loads(interview.questions_json)
    return render_template("interview_session.html", 
                           session_id=session_id, 
                           questions=questions)



@app.route("/submit_answer/<session_id>", methods=["POST"])
def submit_answer(session_id):
    interview = Interview.query.filter_by(session_id=session_id).first()
    if not interview:
        return {"status": "error", "message": "Invalid session"}, 404

    data = request.get_json()
    answers = data.get("answers", [])
    
    # Store answers
    interview.answers_json = json.dumps(answers)
    
    # Determine status based on completion
    all_questions = json.loads(interview.questions_json)
    if len(answers) < len(all_questions):
        interview.status = "Early Submission"
    else:
        interview.status = "Completed"

    db.session.commit()
    return {"status": "success"}

@app.route("/report/<session_id>")
@login_required
def view_report(session_id):
    interview = Interview.query.filter_by(session_id=session_id).first()
    if not interview or not interview.answers_json:
        return "No report available for this session.", 404
    
    all_questions = json.loads(interview.questions_json)
    all_answers = json.loads(interview.answers_json)
    
    # Converting to a list is safer for Jinja2 templates
    answered_pairs = list(zip(all_questions, all_answers))
    
    flags = json.loads(interview.proctoring_flags) if interview.proctoring_flags else []
    
    return render_template("report.html", 
                           interview=interview, 
                           answered_pairs=answered_pairs, 
                           flags=flags)



@app.route("/log_violation/<session_id>", methods=["POST"])
def log_violation(session_id):
    interview = Interview.query.filter_by(session_id=session_id).first()
    if not interview or interview.status != "Pending":
        return {"status": "error"}, 404

    data = request.get_json()
    
    # FIX: Handle cases where proctoring_flags might be empty or None
    try:
        flags = json.loads(interview.proctoring_flags) if interview.proctoring_flags else []
    except:
        flags = []

    flags.append({
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "violation": data.get("type"),
        "details": f"Occurrence #{data.get('count', 1)}"
    })
    
    interview.proctoring_flags = json.dumps(flags)
    db.session.commit()
    return {"status": "success"}

@app.route("/evaluate/<session_id>", methods=["POST", "GET"]) # Added GET just in case you're testing via URL
@login_required
def evaluate_candidate(session_id):
    interview = Interview.query.filter_by(session_id=session_id).first()
    if not interview or not interview.answers_json:
        return redirect(url_for('recruiter_dashboard'))

    # 1. Parse the answers
    answers = json.loads(interview.answers_json)
    
    # 2. Extract the proctoring flags (This is the missing "flags" argument)
    flags = json.loads(interview.proctoring_flags) if interview.proctoring_flags else []
    
    # 3. Pass ALL THREE required arguments to the function
    evaluation = evaluate_performance(interview.jd_text, answers, flags)
    
    # 4. Save and commit
    interview.evaluation_json = json.dumps(evaluation)
    db.session.commit()
    
    return redirect(url_for('view_report', session_id=session_id))


import base64

@app.route("/audit_face_violation/<session_id>", methods=["POST"])
def audit_face_violation_route(session_id):
    interview = Interview.query.filter_by(session_id=session_id).first()
    
    # Safety Check: Ignore requests if the interview isn't active
    if not interview or interview.status != "Pending":
        return jsonify({"verdict": "IGNORE"}), 404

    data = request.get_json()
    try:
        # 1. Extract the image from the request
        image_b64 = data.get('image').split(",")[1]
        
        # 2. Call the new Strict AI Judge from gemini_utils
        verdict = audit_face_violation(image_b64)

        # 3. Load existing flags (handling potential JSON errors)
        try:
            flags = json.loads(interview.proctoring_flags) if interview.proctoring_flags else []
        except:
            flags = []

        # 4. If the AI confirms a violation (Multiple faces or unauthorized devices)
        if verdict == "TERMINATE":
            # Add a detailed flag for the Recruiter Report
            flags.append({
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "violation": "AI_CONFIRMED_PERSON",
                "details": "Gemini Pro verified an unauthorized person/device against a non-plain background."
            })
            interview.proctoring_flags = json.dumps(flags)

            # Count ONLY the AI-verified violations
            ai_violation_count = len([f for f in flags if f['violation'] == 'AI_CONFIRMED_PERSON'])

            # 5. Threshold Logic: 3 AI confirmations = Automatic Cancellation
            if ai_violation_count >= 3:
                interview.status = "Cancelled"
                db.session.commit()
                return jsonify({
                    "verdict": "TERMINATE", 
                    "current_count": ai_violation_count
                })
            
            # If under the limit, save the flag and notify the frontend to show a warning
            db.session.commit()
            return jsonify({
                "verdict": "FLAGGED", 
                "current_count": ai_violation_count
            })

    except Exception as e:
        print(f"❌ Error during AI Face Audit: {e}")
            
    # If the AI says 'STAY' or there's an error, let the candidate continue
    return jsonify({"verdict": "STAY"})

# Custom filter to parse JSON inside HTML templates
@app.template_filter('from_json')
def from_json_filter(s):
    if not s:
        return []
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return []


if __name__ == "__main__":
    # Get port from environment, default to 5000 for local testing
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)