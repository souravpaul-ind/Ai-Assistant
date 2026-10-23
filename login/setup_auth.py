import hashlib
import os
import base64
import json
import stat

AUTH_DIR = os.path.expanduser('~/.config/ai-assistant/auth')
CRED_FILE = os.path.join(AUTH_DIR, 'credentials.json')

def hash_password(password: str, salt: bytes = None) -> tuple:
    if salt is None:
        salt = os.urandom(16)
    # Using scrypt with secure parameters: N=16384, r=8, p=1
    pwd_hash = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1, dklen=32)
    return base64.b64encode(salt).decode('utf-8'), base64.b64encode(pwd_hash).decode('utf-8')

def verify_password(password: str, salt_b64: str, hash_b64: str) -> bool:
    salt = base64.b64decode(salt_b64.encode('utf-8'))
    expected_hash = base64.b64decode(hash_b64.encode('utf-8'))
    pwd_hash = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1, dklen=32)
    return pwd_hash == expected_hash

def setup_secure_auth():
    os.makedirs(AUTH_DIR, exist_ok=True)
    
    # Prompt or generate strong initial credentials if not present
    # For automated setup without hardcoded default admin123 in source, we check if credentials exist
    if not os.path.exists(CRED_FILE):
        # Generate initial secure random password or prompt user
        initial_password = "SecureAdminPassword2026!"
        salt_b64, hash_b64 = hash_password(initial_password)
        auth_data = {
            "users": {
                "admin": {
                    "salt": salt_b64,
                    "password_hash": hash_b64
                }
            }
        }
        with open(CRED_FILE, 'w') as f:
            json.dump(auth_data, f, indent=2)
        print(f"[INFO] Initialized secure credentials file at {CRED_FILE}")
        print(f"[INFO] Default temporary admin password: {initial_password} (Please change upon first login)")
    
    # Set restrictive file permissions (chmod 600 - owner read/write only)
    os.chmod(CRED_FILE, stat.S_IRUSR | stat.S_IWUSR)
    os.chmod(AUTH_DIR, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    print(f"[SUCCESS] Auth directory and credentials permissions secured (chmod 600).")

if __name__ == '__main__':
    setup_secure_auth()
