import getpass
from app import create_app
from app.extensions import db
from app.models.user import User
from app.utils.validators import validate_password
import bcrypt

app=create_app()
with app.app_context():
    db.create_all()
    name=input("Administrator name: ").strip()
    email=input("Administrator email: ").strip().lower()
    password=getpass.getpass("Password (minimum 12 characters recommended): ")
    problem=validate_password(password)
    if not name or not email or problem: raise SystemExit(problem or "Name and email are required.")
    if User.query.filter_by(email=email).first(): raise SystemExit("An account with that email already exists.")
    user=User(name=name,email=email,password_hash=bcrypt.hashpw(password.encode(),bcrypt.gensalt()).decode(),role="admin")
    db.session.add(user); db.session.commit()
    print(f"Admin account created for {email}.")
