import os
import uuid

import json
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Security, Form
from typing import Optional
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import ai_service
from pydantic import ValidationError, BaseModel
import jwt
from jwt import PyJWKClient
from dotenv import load_dotenv
import datetime
from supabase import create_client, Client
import stripe
from fastapi import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import database
import db_models
import journey_models  # PR Journey system models
from sqlalchemy.orm import Session

# Create uploads directory
UPLOADS_DIR = Path(__file__).parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
stripe.api_version = "2024-06-20"  # Pin for stability — avoids Dahlia breaking changes
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# Dev cache mode — set DEV_CACHE_MODE=1 in .env to avoid burning API credits
DEV_CACHE_MODE = os.getenv("DEV_CACHE_MODE", "0") == "1"
DEV_CACHE_DIR = Path(__file__).parent / ".dev_cache"
if DEV_CACHE_MODE:
    DEV_CACHE_DIR.mkdir(exist_ok=True)
    print("⚡ DEV_CACHE_MODE is ON — AI responses will be cached locally to save API credits.")

import json as json_module
def _load_cache(cache_name: str):
    """Load a cached JSON response if it exists."""
    cache_file = DEV_CACHE_DIR / f"{cache_name}.json"
    if cache_file.exists():
        with open(cache_file, 'r', encoding='utf-8') as f:
            print(f"  📦 Cache HIT: {cache_name}")
            return json_module.load(f)
    return None

def _save_cache(cache_name: str, data: dict):
    """Save a JSON response to cache."""
    cache_file = DEV_CACHE_DIR / f"{cache_name}.json"
    with open(cache_file, 'w', encoding='utf-8') as f:
        json_module.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  💾 Cache SAVED: {cache_name}")

# Setup Clerk JWKS client
CLERK_ISSUER_URL = os.getenv("CLERK_ISSUER_URL")

# Setup Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Create tables
db_models.Base.metadata.create_all(bind=database.engine)
journey_models.PRJourney.__table__.create(bind=database.engine, checkfirst=True)
journey_models.DocumentItem.__table__.create(bind=database.engine, checkfirst=True)
journey_models.DrawResult.__table__.create(bind=database.engine, checkfirst=True)
journey_models.NOCCategoryMapping.__table__.create(bind=database.engine, checkfirst=True)

ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp'}

app = FastAPI(
    title="Mentor Visa Analyzer API",
    description="Express Entry toolkit API — NOC Finder & Employment Letter Auditor (OpenAI + Claude), AI Profile Assistant (Gemini).",
    version="2.0.0"
)

# Allow React frontend to connect to this API
# Setup Rate Limiter (IP Based)
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

from fastapi.responses import JSONResponse

async def custom_rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    auth_header = request.headers.get("Authorization")
    is_signed_in = False
    tier = "free"
    
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        try:
            import jwt
            claims = jwt.decode(token, options={"verify_signature": False})
            user_id = claims.get("sub")
            if user_id:
                is_signed_in = True
                # Query DB to get subscription tier
                from database import SessionLocal
                import db_models
                db = SessionLocal()
                try:
                    user = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
                    tier = user.subscription_tier if user else "free"
                except Exception:
                    tier = "free"
                finally:
                    db.close()
        except Exception:
            pass

    if not is_signed_in:
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Rate limit exceeded (3 requests per hour). Sign up/in to increase your hourly limit to 5 searches!",
                "action": "SIGN_IN"
            }
        )
    elif tier in ("starter", "complete"):
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Rate limit exceeded (15 requests per hour for premium tiers). Please try again in an hour.",
                "action": "WAIT"
            }
        )
    else:
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Rate limit exceeded (5 requests per hour for free accounts). Upgrade to Optimize or Execute tier to increase your limit to 15 searches!",
                "action": "UPGRADE"
            }
        )

app.add_exception_handler(RateLimitExceeded, custom_rate_limit_exceeded_handler)

# Register Journey routes
from journey_routes import router as journey_router
app.include_router(journey_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://mentorvisa.com",
        "https://www.mentorvisa.com",
        "http://localhost:5173", # Dev frontend
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def dynamic_rate_limit_key(request: Request) -> str:
    # Default to IP address
    ip = get_remote_address(request)
    
    # Try to extract JWT token from Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        try:
            import jwt
            # Decode without verification for speed (the route dependency performs verification)
            claims = jwt.decode(token, options={"verify_signature": False})
            user_id = claims.get("sub")
            if user_id:
                # Query DB to get user's subscription tier
                from database import SessionLocal
                import db_models
                db = SessionLocal()
                try:
                    user = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
                    tier = user.subscription_tier if user else "free"
                except Exception:
                    tier = "free"
                finally:
                    db.close()
                return f"user:{user_id}:{tier}"
        except Exception:
            pass
    return f"anon:{ip}"

# ── Per-account abuse throttle (Terms of Service s.13 — Acceptable Use / Anti-Abuse) ──────────
# A single seat used as a commercial service (running our AI tools over many different applicants'
# documents) gets a hard hourly cap that overrides any tier benefit. Add/remove Clerk user ids via
# the THROTTLED_USER_IDS env var on Render — comma-separated, no code deploy needed.
THROTTLED_USER_IDS = {
    u.strip() for u in os.getenv(
        "THROTTLED_USER_IDS",
        # 480 tool runs between 2026-07-12 and 2026-08-07 (~105/day) on one Optimize seat.
        "user_3GQNLS5lVcNtZbLIck32x9xBo4K",
    ).split(",") if u.strip()
}
THROTTLED_MAX_PER_HOUR = int(os.getenv("THROTTLED_MAX_PER_HOUR", "1"))
_THROTTLE_MESSAGE = (
    "You've reached this account's hourly limit. Mentor Visa is licensed for your own permanent-"
    "residence application — not for running large numbers of other applicants' documents. "
    "If you need multi-client access, contact info@mentorvisa.com."
)


def enforce_abuse_throttle(user_id: str) -> None:
    """Hard hourly cap for flagged accounts, counted from the evaluations table.

    Normal users pay nothing for this: the id check short-circuits before any DB work. This backs up
    the slowapi limit below, which lives in memory per worker and therefore resets on every Render
    restart — a determined abuser can simply outlast it. Counting saved evaluations instead survives
    restarts and is shared across workers.
    """
    if not user_id or user_id == "anonymous" or user_id not in THROTTLED_USER_IDS:
        return
    from sqlalchemy import func
    db = database.SessionLocal()
    try:
        since = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
        used = db.query(func.count(db_models.Evaluation.id)).filter(
            db_models.Evaluation.user_id == user_id,
            db_models.Evaluation.timestamp_utc >= since,
        ).scalar() or 0
    except Exception as e:
        print(f"[throttle] count failed for {user_id} (allowing request): {e}")
        return
    finally:
        db.close()
    if used >= THROTTLED_MAX_PER_HOUR:
        print(f"[throttle] BLOCKED {user_id}: {used} run(s) in the last hour "
              f"(cap {THROTTLED_MAX_PER_HOUR})")
        raise HTTPException(status_code=429, detail=_THROTTLE_MESSAGE)


def dynamic_rate_limit_value(key: str) -> str:
    # Dev mode bypass — don't rate-limit during local development
    if DEV_CACHE_MODE:
        return "1000/hour"
    if key.startswith("anon:"):
        return "3/hour"
    parts = key.split(":")
    # Flagged accounts are capped ahead of any tier benefit (key format: user:<id>:<tier>).
    if len(parts) >= 2 and parts[1] in THROTTLED_USER_IDS:
        return f"{THROTTLED_MAX_PER_HOUR}/hour"
    if len(parts) >= 3:
        tier = parts[2]
        if tier in ("starter", "complete"):
            return "15/hour"
    return "5/hour"

# --- Auth Dependency ---
# Defined BEFORE the first endpoint that uses it in a Depends() — decorators resolve
# these names at import time, so ordering matters (a late definition breaks startup).
security = HTTPBearer()

# Cache the JWKS client globally to avoid re-creating on every request
_jwks_client = None
def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None and CLERK_ISSUER_URL:
        jwks_url = f"{CLERK_ISSUER_URL}/.well-known/jwks.json"
        _jwks_client = PyJWKClient(jwks_url)
    return _jwks_client

def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials

    try:
        if not CLERK_ISSUER_URL:
            # Dev fallback: decode without signature verification
            claims = jwt.decode(token, options={"verify_signature": False})
            user_id = claims.get("sub")
            if not user_id:
                raise HTTPException(status_code=401, detail="No user ID in token")
            return user_id

        jwks_client = _get_jwks_client()
        if not jwks_client:
            raise HTTPException(status_code=500, detail="JWKS client not configured")

        signing_key = jwks_client.get_signing_key_from_jwt(token)
        data = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=CLERK_ISSUER_URL,
            options={"verify_signature": True}
        )
        user_id = data.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="No user ID in token")
        return user_id
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        # Short-lived Clerk token lapsed (e.g. a long GCMS upload). Never surface the raw
        # "Signature has expired" — users mid-upload read it as their document's signature.
        raise HTTPException(status_code=401,
                            detail="Your session timed out. Please refresh the page and try again — your work is safe.")
    except Exception as e:
        print(f"[AUTH] Token verification failed: {e}")  # keep the real reason in logs only
        raise HTTPException(status_code=401, detail="Authentication failed. Please refresh the page and sign in again.")

def get_current_user_optional(credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))):
    if not credentials:
        return "anonymous"
    try:
        return get_current_user(credentials)
    except Exception:
        return "anonymous"


@app.post("/api/v1/analyze")
@limiter.limit(dynamic_rate_limit_value, key_func=dynamic_rate_limit_key)
async def analyze_document_endpoint(
    request: Request,
    document: UploadFile = File(...),
    target_noc: Optional[str] = Form(None),
    user_id: str = Depends(get_current_user_optional)
):
    """
    Accepts a document (PDF, Word, or Image).
    The AI Service auto-detects the NOC code and evaluates the document against the NOC 2021 Source of Truth.
    Saves the original file to disk and injects the file reference into the response.
    """
    enforce_abuse_throttle(user_id)   # ToS s.13 — flagged accounts capped before any work is done
    filename = document.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400, 
            detail=f"Unsupported file type '{ext}'. Accepted formats: PDF, Word (.docx), and images (JPG, PNG, etc.)."
        )
        
    try:
        doc_bytes = await document.read()
        
        # Enforce 5MB max file size
        MAX_FILE_SIZE = 5 * 1024 * 1024
        if len(doc_bytes) > MAX_FILE_SIZE:
             raise HTTPException(
                status_code=413, 
                detail="File too large. Maximum file size allowed is 5MB."
             )
             
        is_image = ext in IMAGE_EXTENSIONS
        
        # Save the original file
        file_id = str(uuid.uuid4())
        stored_filename = f"{file_id}{ext}"
        
        if supabase:
            _MIME_MAP = {
                '.pdf': 'application/pdf',
                '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                '.doc': 'application/msword',
            }
            content_type = _MIME_MAP.get(ext, f"image/{ext.replace('.', '')}")
            supabase.storage.from_("documents").upload(
                path=stored_filename,
                file=doc_bytes,
                file_options={"content-type": content_type}
            )
        else:
            file_path = UPLOADS_DIR / stored_filename
            with open(file_path, "wb") as f:
                f.write(doc_bytes)
        
        # DEV CACHE: return cached response if available
        if DEV_CACHE_MODE:
            cached = _load_cache("analyze")
            if cached:
                cached["stored_file_id"] = file_id
                cached["original_filename"] = filename
                # Still save to DB so unlock flow works
                db = database.SessionLocal()
                try:
                    record = db_models.Evaluation(
                        evaluation_type='audit',
                        user_id="anonymous",
                        document_type=cached.get("document_type", "Unknown"),
                        role_name=cached.get("role_name", "Unknown Role"),
                        company_name=cached.get("company_name", "Unknown Company"),
                        original_filename=filename,
                        stored_file_id=file_id,
                        compliance_status=cached.get("decision", cached.get("compliance_status", "Unknown")),
                        payload=cached,
                    )
                    db.add(record)
                    db.commit()
                except Exception as log_err:
                    print(f"Warning: failed to auto-log cached evaluation: {log_err}")
                finally:
                    db.close()
                return cached

        # --- MIGRATION TO OPENAI RAG ---
        user_content, page_images = ai_service.extract_document_content(doc_bytes, ext, is_image)
        
        # Auto-detect NOC using the NOC Finder pipeline when no target is specified.
        # This guarantees the auditor uses the EXACT same NOC detection as the NOC Finder.
        auto_detected = None
        if not target_noc:
            target_noc = ai_service.auto_detect_noc(user_content, page_images)
            auto_detected = target_noc  # Remember this was auto-detected, not user-specified

        top_nocs = ai_service.semantic_search_nocs(user_content)

        # The Auditor evaluates against a determined NOC; if auto-detection failed, fall back to the
        # top semantic candidate so we never run the prompt's from-scratch detection path.
        if not target_noc and top_nocs:
            target_noc = next(iter(top_nocs))

        # Auditor Fix: Always include the target_noc in the reference sheet so the AI can evaluate against it!
        if target_noc:
            target_data = ai_service.NOC_CODE_TO_ENTRY.get(target_noc)
            if target_data:
                top_nocs[target_noc] = target_data
                
        noc_reference = json.dumps(top_nocs, ensure_ascii=False)
        system_prompt = ai_service._build_prompt_text(noc_reference, target_noc)
        
        try:
            result_json = ai_service.audit_document_with_openai(
                system_prompt=system_prompt,
                user_content=user_content,
                page_images=page_images,
                auto_detected_noc=auto_detected
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"OpenAI analysis failed: {str(e)}")
        
        # Inject file metadata into the response
        result_json["stored_file_id"] = file_id
        result_json["original_filename"] = filename
        # Persist the extracted text in the saved payload so re-evaluations reuse it instead of
        # re-downloading + re-extracting (and re-paying vision OCR). Stripped from the API response.
        result_json["_extracted_text"] = user_content

        # Save to dev cache for future re-use
        if DEV_CACHE_MODE:
            _save_cache("analyze", result_json)

        # Auto-log every analysis to the database for admin review
        db = database.SessionLocal()
        try:
            record = db_models.Evaluation(
                evaluation_type='audit',
                user_id="anonymous",
                document_type=result_json.get("document_type", "Unknown"),
                role_name=result_json.get("role_name", "Unknown Role"),
                company_name=result_json.get("company_name", "Unknown Company"),
                original_filename=filename,
                stored_file_id=file_id,
                compliance_status=result_json.get("decision", "Unknown"),
                payload=result_json,
            )
            db.add(record)
            db.commit()
        except Exception as log_err:
            print(f"Warning: failed to auto-log evaluation: {log_err}")
        finally:
            db.close()

        result_json.pop("_extracted_text", None)
        return result_json
        
    except ValidationError as ve:
        raise HTTPException(status_code=500, detail=f"Model JSON Validation Error: {ve}")
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"AI Processing failed: {str(e)}")

@app.get("/health")
def health_check():
    return {"status": "ok", "message": "Mentor Visa API is running"}

# --- DB Endpoints ---

# New signed-in users get this many free FULL NOC Finder reports before the result is gated
# to a teaser (code + confidence + summary) with the full breakdown behind Optimize.
NEW_USER_FINDER_CREDITS = 2


def ensure_user_exists(user_id: str, db: Session):
    """Create a UserAccount row if one doesn't exist yet (idempotent).
    Must be called before inserting any row with a FK to users.user_id.
    New users receive their free NOC Finder reports + 5 free AI Assistant credits."""
    if not user_id or user_id == "anonymous":
        return
    existing = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
    if not existing:
        db.add(db_models.UserAccount(user_id=user_id, find_noc_credits=NEW_USER_FINDER_CREDITS, audit_letter_credits=0, profile_builder_credits=5))
        db.commit()

@app.post("/api/v1/evaluations")
def save_evaluation(
    payload: dict,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db)
):
    try:
        ensure_user_exists(user_id, db)
        doc_type = payload.get("document_type", "Unknown")
        compliance = payload.get("decision", payload.get("compliance_status", "Unknown"))
        role_name = payload.get("role_name", "Unknown Role")
        company_name = payload.get("company_name", "Unknown Company")
        
        original_filename = payload.get("original_filename", None)
        stored_file_id = payload.get("stored_file_id", None)
        
        eval_type = payload.get("evaluation_type")
        if not eval_type:
            eval_type = 'noc_finder' if (doc_type == "NOC Finder Query") else 'audit'

        # CRS calculator: always create a new record to maintain full history.
        # The latest calculation is determined by the most recent created_at timestamp.

        # UPSERT: If a record with this stored_file_id already exists, claim it
        # for the current user instead of creating a duplicate.
        if stored_file_id:
            Model = db_models.Evaluation
            existing = db.query(Model).filter_by(stored_file_id=stored_file_id, evaluation_type=eval_type).first()
            if existing:
                # If it's owned by someone else, do not allow modifications
                if existing.user_id != "anonymous" and existing.user_id != user_id:
                    return {"success": True, "id": existing.id}
                    
                # Update payload to the latest state from frontend, but preserve server-side
                # artifacts the client never receives (they are stripped from API responses,
                # so the incoming copy would silently destroy the reuse caches).
                if isinstance(existing.payload, dict) and isinstance(payload, dict):
                    for _k in ("_audit_full", "_extracted_text"):
                        if _k in existing.payload and _k not in payload:
                            payload[_k] = existing.payload[_k]
                existing.payload = payload
                
                # Claim anonymous records for this user
                if existing.user_id == "anonymous":
                    existing.user_id = user_id
                    
                # Only automatically unlock free tools (like NOC Finder or CRS Calculator)
                if eval_type in ["noc_finder", "crs_calculator"]:
                    existing.is_premium_unlocked = 1 
                
                db.commit()
                return {"success": True, "id": existing.id}

        # No existing record found — create a new one
        is_unlocked = 1 if payload.get("is_premium_unlocked") else 0
        if eval_type in ["noc_finder", "crs_calculator"]:
            is_unlocked = 1  # Always unlocked

        record = db_models.Evaluation(
            evaluation_type=eval_type,
            user_id=user_id,
            document_type=doc_type,
            role_name=role_name,
            company_name=company_name,
            original_filename=original_filename,
            stored_file_id=stored_file_id,
            compliance_status=compliance,
            is_premium_unlocked=is_unlocked,
            payload=payload
        )

        db.add(record)
        db.commit()
        db.refresh(record)
        return {"success": True, "id": record.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/evaluations")
def get_evaluations(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db)
):
    audit_records = db.query(db_models.Evaluation).filter_by(evaluation_type='audit', user_id=user_id).all()

    noc_records = db.query(db_models.Evaluation).filter_by(evaluation_type='noc_finder', user_id=user_id).all()

    crs_records = db.query(db_models.Evaluation).filter_by(evaluation_type='crs_calculator', user_id=user_id).all()

    all_records = audit_records + noc_records + crs_records

    # Current entitlement: paid tiers see every saved report in full; free users only see the
    # NOC reports that were unlocked when created (is_premium_unlocked). Locked NOC reports are
    # gated here too, so the history view can't be used to bypass the paywall.
    ua = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
    is_paid = bool(ua and ua.subscription_tier in ("starter", "complete"))

    def _gate_noc_payload(r):
        payload = r.payload
        if not isinstance(payload, dict):
            return payload
        # Never send server-side artifacts (stored full Auditor result, persisted extracted text).
        if "_audit_full" in payload or "_extracted_text" in payload:
            payload = {k: v for k, v in payload.items() if k not in ("_audit_full", "_extracted_text")}
        if r.evaluation_type != 'noc_finder' or is_paid or r.is_premium_unlocked:
            return payload
        gated = dict(payload)
        gated["gaps_count"] = len(gated.get("key_gaps") or [])
        gated["alt_count"] = len(gated.get("alternatives") or [])
        gated["breakdown_count"] = len(gated.get("duties_breakdown") or [])
        gated["key_gaps"] = []
        gated["alternatives"] = []
        gated["duties_breakdown"] = []
        gated["gated"] = True
        gated["gate_reason"] = "upgrade"
        return gated

    result = []
    for r in all_records:
        result.append({
            "id": r.id,
            "document_type": r.document_type,
            "role_name": r.role_name,
            "company_name": r.company_name,
            "original_filename": r.original_filename,
            "stored_file_id": r.stored_file_id,
            "compliance_status": r.compliance_status,
            "is_premium_unlocked": bool(r.is_premium_unlocked),
            "timestamp": (r.timestamp_utc.isoformat() + 'Z') if r.timestamp_utc else None,
            "payload": _gate_noc_payload(r),
        })
        
    # Sort in memory descending by timestamp
    result.sort(key=lambda x: x["timestamp"] or '', reverse=True)
    
    
    
    return {"evaluations": result}

@app.get("/api/v1/documents/{file_id}")
def download_document(
    file_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db)
):
    """Download the original uploaded document. Only the owner can access it."""
    record = db.query(db_models.Evaluation).filter(
        db_models.Evaluation.evaluation_type == 'audit',
        db_models.Evaluation.user_id == user_id,
        db_models.Evaluation.stored_file_id == file_id
    ).first()
    
    if not record:
        record = db.query(db_models.Evaluation).filter(
            db_models.Evaluation.evaluation_type == 'noc_finder',
            db_models.Evaluation.user_id == user_id,
            db_models.Evaluation.stored_file_id == file_id
        ).first()
        
    if not record:
        raise HTTPException(status_code=404, detail="Document not found or access denied.")
    
    # Resolve the actual storage file_id — re-evaluation records store a synthetic ID
    # but the real file lives under the original_file_id
    actual_file_id = file_id
    if "_reeval_" in file_id:
        payload = record.payload if isinstance(record.payload, dict) else {}
        actual_file_id = payload.get("original_file_id", file_id.split("_reeval_")[0])
    
    if supabase:
        from fastapi.responses import RedirectResponse
        ext = os.path.splitext(record.original_filename)[1].lower() if record.original_filename else ".pdf"
        stored_filename = f"{actual_file_id}{ext}"
        try:
            sign_res = supabase.storage.from_("documents").create_signed_url(stored_filename, 3600)
            url = sign_res.get("signedURL") if isinstance(sign_res, dict) else sign_res
            if url:
                return RedirectResponse(url)
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"File not securely found in cloud: {e}")

    # Fallback to local disk
    for ext in ALLOWED_EXTENSIONS:
        candidate = UPLOADS_DIR / f"{actual_file_id}{ext}"
        if candidate.exists():
            return FileResponse(
                path=str(candidate),
                filename=record.original_filename or f"document{ext}",
                media_type="application/octet-stream"
            )
    
    raise HTTPException(status_code=404, detail="File not found on server or cloud.")

class ReevaluateRequest(BaseModel):
    file_id: str
    target_noc: str
    mode: str = "audit"  # "audit" or "noc_finder"

@app.post("/api/v1/reevaluate")
def reevaluate_document(
    req: ReevaluateRequest,
    user_id: str = Depends(get_current_user_optional),
    db: Session = Depends(database.get_db)
):
    """Re-runs the AI analysis on an already uploaded document, forcing a specific NOC code."""
    enforce_abuse_throttle(user_id)   # ToS s.13 — this path re-runs the full pipeline, so it is
                                      # capped too; otherwise it is a free bypass of the limits above
    ensure_user_exists(user_id, db)
    record = db.query(db_models.Evaluation).filter_by(evaluation_type='audit', stored_file_id=req.file_id).first()
    
    if not record:
        record = db.query(db_models.Evaluation).filter_by(evaluation_type='noc_finder', stored_file_id=req.file_id).first()

    if not record:
        raise HTTPException(status_code=404, detail="Document not found.")

    if record.user_id != "anonymous" and record.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied. You do not own this document.")

    
    ext = os.path.splitext(record.original_filename)[1].lower() if record.original_filename else ".pdf"
    is_image = ext in IMAGE_EXTENSIONS
    doc_bytes = None
    
    # Resolve actual storage file_id — re-evaluation records have synthetic IDs
    actual_file_id = req.file_id
    if "_reeval_" in req.file_id:
        payload = record.payload if isinstance(record.payload, dict) else {}
        actual_file_id = payload.get("original_file_id", req.file_id.split("_reeval_")[0])
    
    # Check if this was a text-only input (no file was uploaded)
    is_text_only = record.original_filename in (None, "", "Text Input")
    # We persist the extracted text on every document run. If it's here, a re-evaluation can proceed
    # even when the original file blob is unretrievable (missing/slow storage) — so a re-eval never
    # dies with "document not found" just because the file couldn't be fetched.
    _persisted_text = (record.payload or {}).get("_extracted_text") if isinstance(record.payload, dict) else None

    if is_text_only:
        # No file to download — the user typed their input manually.
        # The original text is stored in the record's payload.
        doc_bytes = None  # Will be handled specially in the noc_finder path below
    elif supabase:
        stored_filename = f"{actual_file_id}{ext}"
        try:
            doc_bytes = supabase.storage.from_("documents").download(stored_filename)
        except Exception as e:
            print(f"[reevaluate] Cloud file fetch failed for {stored_filename}: {e}")
            doc_bytes = None
    else:
        file_path = UPLOADS_DIR / f"{actual_file_id}{ext}"
        if file_path.exists():
            with open(file_path, "rb") as f:
                doc_bytes = f.read()

    if not doc_bytes and not is_text_only and not _persisted_text:
        raise HTTPException(status_code=404, detail="Original file content could not be found.")
        
    try:
        if req.mode == "noc_finder":
            # NOC Finder re-evaluation: use the NOC Finder prompt + schema
            import json as _json
            from models import NOCFinderResponseSchema
            
            user_content = ""
            page_images = []
            
            if is_text_only:
                # Reconstruct the user's original typed input from the stored payload
                payload = record.payload if isinstance(record.payload, dict) else {}
                original_title = payload.get("user_input_job_title", payload.get("role_name", "Unknown Role"))
                original_duties = payload.get("user_input_duties", "")
                user_content = f"Job Title: {original_title}\n\nDuties and Responsibilities:\n{original_duties}"
                print(f"Re-evaluating text-only input: title='{original_title}', duties length={len(original_duties)}")
            else:
                user_content, page_images = ai_service.extract_document_content(doc_bytes, ext, is_image)
            
            try:
                import openai
                # Re-evaluation always runs against a stored uploaded document (from_document=True).
                # Targeted (target_noc) and auto-detect both go through the v2 pipeline so the result
                # (semantic confidence + duty coverage + duty-by-duty) is consistent with a fresh search.
                import noc_finder_v2
                _tgt = req.target_noc if (req.target_noc and req.target_noc != 'auto') else None
                result_json = noc_finder_v2.run_noc_finder_v2(
                    user_content, page_images if page_images else None,
                    target_noc=_tgt, from_document=True,
                )
            except openai.RateLimitError:
                raise HTTPException(status_code=429, detail="OpenAI Rate Limit Exceeded. Please try again later.")
            except openai.APIError as e:
                raise HTTPException(status_code=502, detail=f"OpenAI API Error: {str(e)}")
            except Exception as e:
                print(f"OpenAI processing error: {e}")
                raise HTTPException(status_code=500, detail=f"AI Processing failed: {str(e)}")
            
            # Generate a unique file_id for this re-evaluation
            reeval_file_id = f"{req.file_id}_reeval_{str(uuid.uuid4())[:8]}"
            result_json["stored_file_id"] = reeval_file_id
            result_json["original_file_id"] = actual_file_id
            result_json["is_signed_in"] = 1  # NOC Finder uses is_signed_in, not is_premium_unlocked
            if not is_text_only:
                result_json["_extracted_text"] = user_content  # chained re-evals skip re-extraction too
            
            # Persist to DB so it shows in My Evaluations
            saved_role = result_json.get("role_name") or record.role_name or "Unknown Role"
            saved_company = result_json.get("company_name") or record.company_name or "N/A"
            new_record = db_models.Evaluation(
                evaluation_type='noc_finder',
                user_id=user_id if user_id else record.user_id,
                document_type="NOC Finder Query",
                role_name=saved_role,
                company_name=saved_company,
                original_filename=record.original_filename,
                stored_file_id=reeval_file_id,
                compliance_status="N/A",
                is_premium_unlocked=1,
                payload=result_json,
            )
            db.add(new_record)
            db.commit()

            result_json.pop("_extracted_text", None)
            return result_json
        else:
            # Default: Auditor re-evaluation
            user_content, page_images = ai_service.extract_document_content(doc_bytes, ext, is_image)

            # Auto-detect NOC using the NOC Finder pipeline when no target is specified.
            effective_target = req.target_noc if (req.target_noc and req.target_noc != 'auto') else None
            auto_detected = None
            if not effective_target:
                effective_target = ai_service.auto_detect_noc(user_content, page_images)
                auto_detected = effective_target

            top_nocs = ai_service.semantic_search_nocs(user_content)

            # Fall back to the top semantic candidate if detection failed, so the Auditor always
            # evaluates against a determined NOC rather than re-detecting from scratch.
            if not effective_target and top_nocs:
                effective_target = next(iter(top_nocs))

            if effective_target:
                target_data = ai_service.NOC_CODE_TO_ENTRY.get(effective_target)
                if target_data:
                    top_nocs[effective_target] = target_data
                    
            noc_reference = json.dumps(top_nocs, ensure_ascii=False)
            system_prompt = ai_service._build_prompt_text(noc_reference, effective_target)

            # Reuse the audit the NOC Finder already computed for this exact file + NOC (the user clicked
            # "Audit my letter" on a Finder result) — avoids paying for a second, identical audit. Try the
            # in-memory cache first (same session), then the persisted Finder payload (works from a past
            # NOC check in the dashboard, across restarts).
            def _audit_matches_target(a):
                # Only reuse a cached/stored audit if it was actually computed against the requested NOC.
                return (isinstance(a, dict) and effective_target
                        and ((a.get("noc_analysis") or {}).get("detected_code") == effective_target))

            result_json = ai_service.pop_finder_audit(req.file_id, effective_target) if effective_target else None
            if not _audit_matches_target(result_json):
                result_json = None
            if result_json is None and effective_target and isinstance(record.payload, dict):
                stored_audit = record.payload.get("_audit_full")
                if _audit_matches_target(stored_audit):
                    result_json = stored_audit
                    print(f"[reevaluate] Reused stored Finder audit (payload) for file={req.file_id} noc={effective_target}")
            if result_json is None:
                # An explicit target (clicked alternative / typed code) must be hard-locked, not just
                # requested via the prompt — pass it as forced_noc so the result's detected_code can't
                # drift to the employer's stated NOC or the model's own second guess.
                explicit_target = req.target_noc if (req.target_noc and req.target_noc != 'auto') else None
                result_json = ai_service.audit_document_with_openai(
                    system_prompt=system_prompt,
                    user_content=user_content,
                    page_images=page_images if page_images else None,
                    auto_detected_noc=auto_detected,
                    forced_noc=explicit_target,
                )
            else:
                print(f"[reevaluate] Reused NOC Finder's audit for file={req.file_id} noc={effective_target}")
            
            # Generate a unique file_id for this reevaluation so it doesn't collide
            # with the original record during UPSERT. Keep original file_id as reference.
            reeval_file_id = f"{req.file_id}_reeval_{str(uuid.uuid4())[:8]}"
            
            result_json["stored_file_id"] = reeval_file_id
            result_json["original_file_id"] = req.file_id  # Reference to original document
            result_json["original_filename"] = record.original_filename
            # Unlock inheritance:
            # - Once THIS letter's audit was unlocked anywhere in its chain (original or any
            #   re-eval), further re-evaluations against other NOC codes are free — the user
            #   already paid for this letter.
            # - Optimize/Execute include unlimited audits, so their re-evals unlock outright.
            # - A NOC Finder source alone does NOT unlock (audits require payment).
            chain_unlocked = db.query(db_models.Evaluation).filter(
                db_models.Evaluation.evaluation_type == 'audit',
                db_models.Evaluation.is_premium_unlocked == 1,
                (db_models.Evaluation.stored_file_id == actual_file_id) |
                (db_models.Evaluation.stored_file_id.startswith(f"{actual_file_id}_reeval_", autoescape=True)),
            ).first() is not None
            ua_re = db.query(db_models.UserAccount).filter_by(user_id=user_id).first() if user_id != "anonymous" else None
            paid_tier = bool(ua_re and ua_re.subscription_tier in ("starter", "complete"))
            audit_unlocked = 1 if (chain_unlocked or paid_tier
                                   or (record.evaluation_type == 'audit' and record.is_premium_unlocked)) else 0
            result_json["is_premium_unlocked"] = audit_unlocked
            
            # Include target NOC in metadata for display in My Evaluations
            if req.target_noc and req.target_noc != 'auto':
                result_json["reevaluated_against_noc"] = req.target_noc

            result_json["_extracted_text"] = user_content  # chained re-evals skip re-extraction too

            # Save as a brand new evaluation run
            new_record = db_models.Evaluation(
                evaluation_type='audit',
                user_id=user_id if user_id else record.user_id,
                document_type=result_json.get("document_type", "Unknown"),
                role_name=result_json.get("role_name", "Unknown Role"),
                company_name=result_json.get("company_name", "Unknown Company"),
                original_filename=record.original_filename,
                stored_file_id=reeval_file_id,
                compliance_status=result_json.get("decision", "Unknown"),
                is_premium_unlocked=audit_unlocked,
                payload=result_json,
            )
            db.add(new_record)
            db.commit()
            db.refresh(new_record)

            result_json.pop("_extracted_text", None)
            return result_json
        
    except ValidationError as ve:
        raise HTTPException(status_code=500, detail=f"Model JSON Validation Error: {ve}")
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"AI Processing failed: {str(e)}")

# --- NOC Finder Tool ---

from fastapi import Form
from typing import Optional
from models import NOCFinderResponseSchema

@app.post("/api/v1/noc-finder")
@limiter.limit(dynamic_rate_limit_value, key_func=dynamic_rate_limit_key)
async def noc_finder_endpoint(
    request: Request,
    job_title: Optional[str] = Form(None),
    duties_description: Optional[str] = Form(None),
    document: Optional[UploadFile] = File(None),
    target_noc: Optional[str] = Form(None),
    user_id: str = Depends(get_current_user_optional),
    db: Session = Depends(database.get_db)
):
    """
    Accepts EITHER a job title and duties description OR a document upload.
    Uses AI to match against all 516 NOC 2021 unit groups and returns the best match with alternatives.
    """
    import json as _json
    enforce_abuse_throttle(user_id)   # ToS s.13 — flagged accounts capped before any work is done
    evaluation_id = str(uuid.uuid4())
    try:
        ensure_user_exists(user_id, db)
        if not document and not (job_title and duties_description):
            raise HTTPException(status_code=400, detail="Provide either a document upload OR both job_title & duties_description.")

        # ── Pre-flight entitlement gate (saves API cost) ──────────────────────
        # A signed-in FREE user with no finder credits cannot run the pipeline — we return the
        # payment gate WITHOUT calling any model. Anonymous users still get one run (the funnel),
        # and paid / credit-holding users run normally. After a successful purchase the frontend
        # re-runs the search (credits now > 0) so the report is produced then.
        is_signed_in = bool(user_id and user_id != "anonymous")
        if is_signed_in:
            _ua = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
            _tier = ((_ua.subscription_tier if _ua else "free") or "free")
            _paid = _tier in ("starter", "complete")
            _credits = (_ua.find_noc_credits if _ua else 0) or 0
            if not _paid and _credits <= 0:
                return {
                    "document_valid": True, "rejection_reason": "",
                    "gated": True, "gate_reason": "upgrade", "requires_payment": True,
                    "recommended_noc": None, "result_type": "NO_MATCH",
                    "finder_credits_remaining": 0, "tier": _tier,
                    "is_signed_in": 1,
                }

        user_content = ""
        is_hybrid = False
        page_images = []
        
        if document:
            filename = document.filename or ""
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"Unsupported file type '{ext}'")
            
            doc_bytes = await document.read()
            
            stored_filename = f"{evaluation_id}{ext}"
            if supabase:
                _MIME_MAP = {
                    '.pdf': 'application/pdf',
                    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    '.doc': 'application/msword',
                }
                content_type = _MIME_MAP.get(ext, f"image/{ext.replace('.', '')}")
                supabase.storage.from_("documents").upload(
                    path=stored_filename,
                    file=doc_bytes,
                    file_options={"content-type": content_type}
                )
            else:
                file_path = UPLOADS_DIR / stored_filename
                with open(file_path, "wb") as f:
                    f.write(doc_bytes)
            
            is_image = ext in IMAGE_EXTENSIONS
            user_content, page_images = ai_service.extract_document_content(doc_bytes, ext, is_image)
        else:
            user_content = f"Job Title: {job_title}\nMain Duties: {duties_description}"

        # DEV CACHE: return cached response if available
        if DEV_CACHE_MODE:
            cached = _load_cache("noc_finder")
            if cached:
                cached["stored_file_id"] = evaluation_id
                cached["is_premium_unlocked"] = 0
                new_record = db_models.Evaluation(
                evaluation_type='noc_finder',
                    user_id=user_id,
                    document_type="NOC Finder Query",
                    role_name=job_title or "Unknown Role",
                    company_name="N/A",
                    original_filename=document.filename if document else "Text Input",
                    stored_file_id=evaluation_id,
                    compliance_status="N/A",
                    is_premium_unlocked=0,
                    payload=cached,
                )
                db.add(new_record)
                db.commit()
                return cached

        try:
            import openai
            # Both auto-detect and targeted (alternative click / typed code) go through v2 so the
            # output is consistent: a targeted code is scored by real semantic precision + duty-by-duty
            # coverage, not the old single-prompt path that self-reported 100/100.
            import noc_finder_v2
            _tgt = target_noc if (target_noc and target_noc != 'auto') else None
            result = noc_finder_v2.run_noc_finder_v2(
                user_content, page_images if page_images else None,
                target_noc=_tgt, from_document=bool(document),
            )
        except openai.RateLimitError as e:
            print(f"OpenAI RateLimitError details: {e.response.json() if hasattr(e, 'response') else str(e)}")
            raise HTTPException(status_code=429, detail=f"OpenAI Rate Limit Exceeded: {str(e)}")
        except openai.APIError as e:
            raise HTTPException(status_code=502, detail=f"OpenAI API Error: {str(e)}")
        except Exception as e:
            print(f"OpenAI processing error: {e}")
            raise HTTPException(status_code=500, detail=f"AI Processing failed: {str(e)}")
        result["stored_file_id"] = evaluation_id
        # The full Auditor result (if the Finder ran one) is kept in `result` so it PERSISTS in the saved
        # payload — that lets "Audit my letter" reuse it even from a past NOC check in the dashboard
        # (and same-session via the in-memory cache). It is stripped from the API RESPONSE after saving,
        # so the paid audit is never sent to the client.
        _audit_full = result.get("_audit_full")
        if _audit_full:
            ai_service.cache_finder_audit(evaluation_id, (result.get("recommended_noc") or {}).get("code"), _audit_full)
        # Persist the extracted text (documents only) so later re-evaluations / "Audit my letter"
        # skip re-download + re-extraction + OCR. Stripped from the API response below.
        if document:
            result["_extracted_text"] = user_content
        # NOC Finder is free for signed-in users
        is_signed_in = user_id and user_id != "anonymous"
        result["is_signed_in"] = 1 if is_signed_in else 0

        # Persist the raw user input so we can review what people are typing
        if job_title:
            result["user_input_job_title"] = job_title
        if duties_description:
            result["user_input_duties"] = duties_description
        
        # Save to dev cache for future re-use
        if DEV_CACHE_MODE:
            _save_cache("noc_finder", result)

        # Save to DB
        # Use AI-extracted role/company from the response, fall back to typed input
        saved_role = result.get("role_name") or job_title or "Unknown Role"
        saved_company = result.get("company_name") or "N/A"
        # ── Monetization gating ──────────────────────────────────────────────
        # Paid tiers (and free users who still have finder credits) get the FULL report.
        # Anonymous users and free users out of credits get a TEASER: the matched NOC,
        # confidence, "why", and aligned duties stay visible, but the gaps + alternative
        # NOCs are stripped from the RESPONSE (the full version is saved to the DB) and gated
        # behind sign-in / Optimize. We decide access BEFORE saving so `is_premium_unlocked`
        # records whether THIS report was unlocked — the history view re-applies the same gate.
        import copy
        ua = db.query(db_models.UserAccount).filter_by(user_id=user_id).first() if is_signed_in else None
        tier = ((ua.subscription_tier if ua else "free") or "free")
        is_paid = tier in ("starter", "complete")
        credits = (ua.find_noc_credits if ua else 0) or 0
        is_new_search = not target_noc  # re-evaluating an alternative NOC does not spend a credit

        full_access = is_paid or (is_signed_in and credits > 0)

        # Spend one free credit on a brand-new full search (never on alternative re-evals)
        if full_access and not is_paid and is_signed_in and is_new_search and credits > 0:
            ua.find_noc_credits = credits - 1
            credits = ua.find_noc_credits

        new_record = db_models.Evaluation(
                evaluation_type='noc_finder',
            user_id=user_id,
            document_type="NOC Finder Query",
            role_name=saved_role,
            company_name=saved_company,
            original_filename=document.filename if document else "Text Input",
            stored_file_id=evaluation_id,
            compliance_status="N/A",
            is_premium_unlocked=1 if full_access else 0,
            payload=result,
        )
        db.add(new_record)
        db.commit()

        # Saved to the DB (payload retains _audit_full + _extracted_text for reuse); strip them from
        # the API response so internal artifacts are never sent to the Finder client.
        result.pop("_audit_full", None)
        result.pop("_extracted_text", None)

        # Expose counts + balance so the teaser can say "see N gaps / M alternatives"
        result["gaps_count"] = len(result.get("key_gaps") or [])
        result["alt_count"] = len(result.get("alternatives") or [])
        result["breakdown_count"] = len(result.get("duties_breakdown") or [])
        result["finder_credits_remaining"] = (credits if (is_signed_in and not is_paid) else None)
        result["tier"] = tier

        if full_access:
            result["gated"] = False
            return result

        # Gated view: hide EVERY identifying field (NOC code, title, summary, breakdown, gaps,
        # alternatives, role/employer) so the matched NOC cannot be learned without paying — only the
        # duty-coverage gauge remains visible. The full result is saved to the DB and returned by
        # /noc-finder/reveal once the user is entitled (signed in with a credit, or paid).
        def _gated_view(reason):
            g = copy.deepcopy(result)
            g.update({
                "gated": True, "gate_reason": reason,
                "recommended_noc": {"code": "", "title": "", "confidence": 0, "duties_total": 0, "duties_matched": 0},
                "why_this_noc": "", "key_matches": [], "key_gaps": [], "alternatives": [],
                "duties_breakdown": [], "coverage_subtitle": "", "role_name": "", "company_name": "",
            })
            return g

        if not is_signed_in:
            return _gated_view("signin")   # anon — sign in to reveal (free users get 2 reports)
        return _gated_view("upgrade")      # signed-in, out of credits — must upgrade/buy a pack

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"NOC Finder failed: {str(e)}")


class NocRevealRequest(BaseModel):
    stored_file_id: str

@app.post("/api/v1/noc-finder/reveal")
def noc_finder_reveal(
    req: NocRevealRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """Decide entitlement when a user signs in after an anonymous search — WITHOUT re-running the AI.
    Paid tiers reveal for free; free users spend ONE finder credit to reveal; users with no credits
    get the upgrade gate. Also claims the anonymous record and records the unlock state so the
    history view can't be used to bypass the paywall."""
    ensure_user_exists(user_id, db)
    ua = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
    tier = ((ua.subscription_tier if ua else "free") or "free")
    is_paid = tier in ("starter", "complete")
    credits = (ua.find_noc_credits if ua else 0) or 0

    rec = db.query(db_models.Evaluation).filter_by(
        evaluation_type='noc_finder', stored_file_id=req.stored_file_id).first()
    # IDEMPOTENT: the frontend may retry the reveal (token races right after sign-up) — a record
    # this user already unlocked must never cost a second credit.
    already_unlocked = bool(rec and rec.is_premium_unlocked and rec.user_id in (user_id, "anonymous"))

    if is_paid or already_unlocked:
        access = "full"
    elif credits > 0:
        ua.find_noc_credits = credits - 1
        credits = ua.find_noc_credits
        access = "full"
    else:
        access = "gated"

    if rec:
        if rec.user_id == "anonymous":
            rec.user_id = user_id          # claim the anon record for this user
        rec.is_premium_unlocked = 1 if access == "full" else 0
    db.commit()

    # On a full reveal, return the FULL saved result (the gated /noc-finder response had the NOC code
    # stripped, so the client needs the real data to render the unlocked report). Strip the internal
    # _audit_full before sending.
    full = None
    if access == "full" and rec and isinstance(rec.payload, dict):
        full = {k: v for k, v in rec.payload.items() if k not in ("_audit_full", "_extracted_text")}
        full["gated"] = False
        full["gate_reason"] = None
        full["finder_credits_remaining"] = (None if is_paid else credits)
        full["tier"] = tier
        full["is_signed_in"] = 1

    return {"access": access, "finder_credits_remaining": (None if is_paid else credits), "tier": tier, "result": full}

# --- Bank Letter Auditor Tool ---

import bank_letter_service

@app.post("/api/v1/bank-letter-audit")
@limiter.limit("5/hour")
async def bank_letter_audit_endpoint(
    request: Request,
    document: UploadFile = File(...),
    user_id: str = Depends(get_current_user_optional),
    db: Session = Depends(database.get_db)
):
    """
    Accepts a bank letter (PDF, Word, or Image) and audits it against IRCC's
    7 required elements for proof of settlement funds.
    """
    ensure_user_exists(user_id, db)
    
    filename = document.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Accepted: PDF, Word (.docx), and images."
        )
    
    try:
        doc_bytes = await document.read()
        
        MAX_FILE_SIZE = 5 * 1024 * 1024
        if len(doc_bytes) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File too large. Maximum 5MB.")
        
        is_image = ext in IMAGE_EXTENSIONS
        
        # Save the original file
        file_id = str(uuid.uuid4())
        stored_filename = f"{file_id}{ext}"
        
        if supabase:
            _MIME_MAP = {
                '.pdf': 'application/pdf',
                '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                '.doc': 'application/msword',
            }
            content_type = _MIME_MAP.get(ext, f"image/{ext.replace('.', '')}")
            supabase.storage.from_("documents").upload(
                path=stored_filename,
                file=doc_bytes,
                file_options={"content-type": content_type}
            )
        else:
            file_path = UPLOADS_DIR / stored_filename
            with open(file_path, "wb") as f:
                f.write(doc_bytes)
        
        # Run the AI audit
        result_json = bank_letter_service.audit_bank_letter(doc_bytes, ext, is_image)
        
        # Inject file metadata
        result_json["stored_file_id"] = file_id
        result_json["original_filename"] = filename
        result_json["evaluation_type"] = "bank_letter_audit"
        
        # Save to DB
        compliance = result_json.get("overall_compliance", "unknown")
        record = db_models.Evaluation(
            evaluation_type='bank_letter_audit',
            user_id=user_id,
            document_type="Bank Letter",
            role_name="Proof of Funds",
            company_name=result_json.get("bank_name", "Unknown Bank"),
            original_filename=filename,
            stored_file_id=file_id,
            compliance_status=compliance,
            is_premium_unlocked=0,  # Requires payment to unlock
            payload=result_json,
        )
        db.add(record)
        db.commit()
        
        return result_json
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bank letter audit failed: {str(e)}")

# --- Letter Builder Tool ---


from letter_builder_models import DutyAnalysisRequest, LetterGenerationRequest

@app.get("/api/v1/letter-builder/noc-duties/{noc_code}")
def get_noc_duties(noc_code: str):
    """RETIRED: the Letter Builder is a legacy product and is no longer offered."""
    raise HTTPException(status_code=410, detail="The Letter Builder has been retired.")
    entry = ai_service.get_noc_details(noc_code)
    if not entry:
        raise HTTPException(status_code=404, detail=f"NOC code {noc_code} not found.")
    
    duties = entry.get("duties", [])
    return {
        "noc_code": noc_code,
        "noc_title": entry.get("title", ""),
        "lead_statement": entry.get("lead_statement", ""),
        "duties": [{"duty_text": d, "index": i} for i, d in enumerate(duties)]
    }


@app.post("/api/v1/letter-builder/analyze-duty")
@limiter.limit("20/hour")
async def analyze_duty_endpoint(
    request: Request,
    req: DutyAnalysisRequest,
    user_id: str = Depends(get_current_user),
):
    """RETIRED: the Letter Builder is a legacy product and is no longer offered."""
    raise HTTPException(status_code=410, detail="The Letter Builder has been retired.")
    if not req.duty_text.strip():
        raise HTTPException(status_code=400, detail="Duty text cannot be empty.")
    if len(req.duty_text) > 2000:
        raise HTTPException(status_code=400, detail="Duty text too long (max 2000 characters).")
    
    try:
        result = ai_service.analyze_single_duty(req.duty_text.strip(), req.noc_code)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Duty analysis failed: {str(e)}")


@app.post("/api/v1/letter-builder/generate-letter")
async def generate_letter_endpoint(
    req: LetterGenerationRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db)
):
    """RETIRED: the Letter Builder is a legacy product and is no longer offered."""
    raise HTTPException(status_code=410, detail="The Letter Builder has been retired.")
    ensure_user_exists(user_id, db)
    
    # Check access: Complete tier gets unlimited, otherwise need credits
    user = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
    has_tier_access = user and user.subscription_tier == 'complete'
    if not has_tier_access:
        if not user or user.letter_builder_credits <= 0:
            raise HTTPException(status_code=403, detail="No Letter Builder credits available. Please upgrade to Complete or purchase a credit.")
        # Only deduct credits for non-tier users
        user.letter_builder_credits -= 1
    
    try:
        result = ai_service.assemble_letter_text(
            employment_details=req.employment_details.model_dump(),
            noc_code=req.noc_code,
            noc_title=req.noc_title,
            approved_duties=[d.model_dump() for d in req.approved_duties]
        )
        
        # Save to evaluations table
        record = db_models.Evaluation(
            evaluation_type='letter_builder',
            user_id=user_id,
            document_type="Letter Builder",
            role_name=req.employment_details.job_title,
            company_name=req.employment_details.company_name,
            original_filename=None,
            stored_file_id=str(uuid.uuid4()),
            compliance_status=result.get("status", "APPROVED"),
            is_premium_unlocked=1,
            payload={
                **result,
                "employment_details": req.employment_details.model_dump(),
                "approved_duties": [d.model_dump() for d in req.approved_duties],
                "noc_code": req.noc_code,
                "noc_title": req.noc_title,
            },
        )
        db.add(record)
        db.commit()
        
        return result
    except Exception as e:
        db.rollback()
        # Refund credit on error
        user.letter_builder_credits += 1
        db.commit()
        raise HTTPException(status_code=500, detail=f"Letter generation failed: {str(e)}")


# --- Monetization / Stripe Endpoints ---

@app.get("/api/v1/user/credits")
def get_user_credits(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db)
):
    """Fetch user credit balance."""
    user = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
    if not user:
        return {"find_noc_credits": 0, "audit_letter_credits": 0, "ita_strategy_credits": 0, "profile_builder_credits": 0}
    return {
        "find_noc_credits": user.find_noc_credits,
        "audit_letter_credits": user.audit_letter_credits,
        "letter_builder_credits": user.letter_builder_credits,
        "ita_strategy_credits": user.ita_strategy_credits,
        "profile_builder_credits": user.profile_builder_credits,
        "gcms_credits": user.gcms_credits or 0,
        "gcms_analyzer_credits": user.gcms_analyzer_credits or 0,
        "subscription_tier": user.subscription_tier or "free"
    }

@app.post("/api/v1/dev/grant-credits")
def dev_grant_credits(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db)
):
    """LOCAL DEV ONLY — grants 5 test credits of each type to the current user."""
    if not DEV_CACHE_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    user = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
    if not user:
        user = db_models.UserAccount(user_id=user_id, find_noc_credits=0, audit_letter_credits=0)
        db.add(user)
    user.find_noc_credits += 5
    user.audit_letter_credits += 5
    user.letter_builder_credits += 5
    user.ita_strategy_credits += 5
    user.profile_builder_credits += 100
    db.commit()
    return {
        "status": "granted",
        "user_id": user_id,
        "find_noc_credits": user.find_noc_credits,
        "audit_letter_credits": user.audit_letter_credits,
        "letter_builder_credits": user.letter_builder_credits,
        "ita_strategy_credits": user.ita_strategy_credits,
        "profile_builder_credits": user.profile_builder_credits
    }

class CheckoutRequest(BaseModel):
    pass_type: str # 'finder' or 'auditor'
    return_path: Optional[str] = None
    return_url: Optional[str] = None
    order_id: Optional[int] = None  # GCMS order to tie the payment to (pass_type='gcms')

@app.post("/api/v1/create-checkout-session")
def create_checkout_session(
    req: CheckoutRequest,
    user_id: str = Depends(get_current_user),
):
    """Create a Stripe checkout session mapping the user strictly to a credit package."""
    # Ensure user row exists before logging payment events (FK constraint)
    checkout_db = database.SessionLocal()
    try:
        ensure_user_exists(user_id, checkout_db)
    finally:
        checkout_db.close()
    if req.pass_type == 'auditor':
        # 24.90 CAD
        amount = 2490
        name = "Employment Letter Audit (1 Use)"
    elif req.pass_type == 'letter_builder':
        # 14.90 CAD
        amount = 1490
        name = "Interactive Letter Builder (1 Use)"
    elif req.pass_type == 'ita_strategy':
        # 19.90 CAD
        amount = 1990
        name = "Personalized ITA Strategy Report (1 Use)"
    elif req.pass_type == 'war_room':
        # 19.00 CAD
        amount = 1900
        name = "CRS Point Simulator & Draw Matcher Unlock"
    elif req.pass_type == 'finder_1':
        # 9.90 CAD — 1 NOC Finder full report
        amount = 990
        name = "NOC Finder — 1 Full Report Credit"
    elif req.pass_type == 'finder_3':
        # 14.90 CAD — 3 NOC Finder full reports
        amount = 1490
        name = "NOC Finder — 3 Full Report Credits"
    elif req.pass_type == 'finder_5':
        # 19.90 CAD — 5 NOC Finder full reports
        amount = 1990
        name = "NOC Finder — 5 Full Report Credits"
    elif req.pass_type == 'gcms':
        # 19.90 CAD — GCMS notes order (ATIP request filed on the applicant's behalf)
        amount = 1990
        name = "GCMS Notes Order — Full IRCC File Request"
    elif req.pass_type == 'gcms_analyzer':
        # 14.90 CAD — AI analysis of an already-obtained GCMS notes PDF (free with our orders)
        amount = 1490
        name = "GCMS Notes AI Analysis (1 Report)"
    elif req.pass_type == 'starter':
        # 49.00 CAD — Optimize tier
        amount = 4900
        name = "Mentor Visa Optimize — Employment Audits + CRS Simulator"
    elif req.pass_type == 'complete':
        # Check if user is upgrading from Starter → pay only the difference
        upgrade_db = database.SessionLocal()
        try:
            existing_user = upgrade_db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
            if existing_user and existing_user.subscription_tier == 'starter':
                # Differential upgrade: $99 - $49 = $50
                amount = 5000
                name = "Mentor Visa Execute Upgrade (from Optimize)"
            else:
                amount = 9900
                name = "Mentor Visa Execute — All Tools + AI Assistant"
        finally:
            upgrade_db.close()
    else:
        # NOC Finder is free for signed-in users — this path shouldn't be hit anymore
        amount = 0
        name = "NOC Finder Pass (1 Use)"

    try:
        success_url = f"{req.return_url}?payment_success=true" if req.return_url else f"{FRONTEND_URL}{req.return_path}?payment_success=true" if req.return_path else f"{FRONTEND_URL}/dashboard?payment_success=true"
        cancel_url = f"{req.return_url}?payment_canceled=true&session_id={{CHECKOUT_SESSION_ID}}" if req.return_url else f"{FRONTEND_URL}{req.return_path}?payment_canceled=true&session_id={{CHECKOUT_SESSION_ID}}" if req.return_path else f"{FRONTEND_URL}/dashboard?payment_canceled=true&session_id={{CHECKOUT_SESSION_ID}}"

        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'cad',
                    'product_data': {
                        'name': name,
                    },
                    'unit_amount': amount,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=user_id, # Safely tie purchase to user explicitly
            metadata={
                "pass_type": req.pass_type,
                **({"order_id": str(req.order_id)} if req.order_id else {}),
            }
        )

        # LOG Payment Initialization
        db = database.SessionLocal()
        try:
            pe = db_models.PaymentEvent(
                user_id=user_id,
                stripe_session_id=session.id,
                event_type='checkout_initiated',
                pass_type=req.pass_type
            )
            db.add(pe)
            # Tie the Stripe session to the GCMS order so we can verify payment even if the
            # webhook is delayed (the GET orders endpoint lazily re-checks the session).
            if req.pass_type == 'gcms' and req.order_id:
                order = db.query(db_models.GCMSOrder).filter_by(id=req.order_id, user_id=user_id).first()
                if order:
                    order.stripe_session_id = session.id
            db.commit()
        except Exception as log_e:
            print(f"Warning: failed to log payment init: {log_e}")
        finally:
            db.close()
        return {"session_url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe Error: {str(e)}")


from fastapi import Request

@app.post("/api/v1/stripe-webhook")
async def stripe_webhook(request: Request, db: Session = Depends(database.get_db)):
    """Handle Stripe Webhooks anonymously"""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError, Exception) as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(e)}")

    if event.type == 'checkout.session.completed':
        session = event.data.object
        client_user_id = getattr(session, "client_reference_id", None)
        meta = getattr(session, "metadata", {}) or {}
        pass_type = meta.get("pass_type") if isinstance(meta, dict) else getattr(meta, "pass_type", None)
        
        # LOG Payment Success
        pe = db.query(db_models.PaymentEvent).filter_by(stripe_session_id=session.id).first()
        if pe:
            pe.event_type = 'checkout_success'
            db.commit()
        
        if client_user_id:
            user = db.query(db_models.UserAccount).filter_by(user_id=client_user_id).first()
            if not user:
                user = db_models.UserAccount(user_id=client_user_id, find_noc_credits=NEW_USER_FINDER_CREDITS, audit_letter_credits=0)
                db.add(user)
            
            if pass_type == 'auditor':
                user.audit_letter_credits += 1
            elif pass_type == 'finder_1':
                user.find_noc_credits += 1
            elif pass_type == 'finder_3':
                user.find_noc_credits += 3
            elif pass_type == 'finder_5':
                user.find_noc_credits += 5
            elif pass_type == 'letter_builder':
                user.letter_builder_credits += 1
            elif pass_type == 'ita_strategy':
                user.ita_strategy_credits += 1
            elif pass_type == 'war_room':
                user.ita_strategy_credits += 1
            elif pass_type == 'gcms':
                # Mark the GCMS order paid -> next step is the signed consent upload.
                order_id = meta.get("order_id") if isinstance(meta, dict) else getattr(meta, "order_id", None)
                order = None
                if order_id:
                    order = db.query(db_models.GCMSOrder).filter_by(id=int(order_id), user_id=client_user_id).first()
                if not order:
                    order = db.query(db_models.GCMSOrder).filter_by(stripe_session_id=session.id).first()
                if order and order.status == 'awaiting_payment':
                    order.status = 'awaiting_consent'
                    user.gcms_analyzer_credits += 1  # every paid order includes 1 free AI analysis
            elif pass_type == 'gcms_analyzer':
                user.gcms_analyzer_credits += 1
            elif pass_type == 'starter':
                user.subscription_tier = 'starter'
                # Starter (Optimize) tier includes some credits
                user.audit_letter_credits += 2
                user.ita_strategy_credits += 2
                user.profile_builder_credits += 20  # 20 AI Assistant questions
                user.gcms_credits += 1              # 1 free GCMS notes order
            elif pass_type == 'complete':
                # Grant differential credits based on whether upgrading from starter
                was_starter = user.subscription_tier == 'starter'
                user.subscription_tier = 'complete'
                if was_starter:
                    # Upgrading from Starter — grant only the difference
                    # Starter gave: 2 audit + 2 strategy + 20 profile builder + 1 GCMS
                    # Complete total: 5 audit + 3 builder + 5 strategy + unlimited profile builder + 1 GCMS
                    # Difference: 3 audit + 3 builder + 3 strategy (GCMS + profile builder already granted at Starter)
                    user.audit_letter_credits += 3
                    user.letter_builder_credits += 3
                    user.ita_strategy_credits += 3
                else:
                    # Fresh Complete purchase — full credits
                    user.audit_letter_credits += 5
                    user.letter_builder_credits += 3
                    user.ita_strategy_credits += 5
                    user.gcms_credits += 1          # 1 free GCMS notes order
            else:
                user.find_noc_credits += 1
                
            db.commit()

    return {"status": "success"}


# ── Contact form ───────────────────────────────────────────────────────────────

CONTACT_SUBJECTS = {
    "Technical issue", "Billing & payments", "GCMS notes order", "Refund request",
    "Feedback & suggestions", "Partnership / business", "Question about my results", "Other",
}


class ContactRequest(BaseModel):
    first_name: str
    last_name: str
    email: str
    subject: str
    message: str
    website: str = ""  # honeypot — real users never fill this hidden field


@app.post("/api/v1/contact")
@limiter.limit("5/hour")
def send_contact_message(request: Request, req: ContactRequest, db: Session = Depends(database.get_db)):
    """Public contact endpoint: store the message, notify the inbox when SMTP is configured."""
    if req.website.strip():  # bot filled the honeypot — pretend success, store nothing
        return {"status": "ok"}
    if not req.first_name.strip() or not req.last_name.strip():
        raise HTTPException(status_code=422, detail="Please provide your first and last name.")
    if "@" not in req.email or "." not in req.email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="Please provide a valid email address.")
    if len(req.message.strip()) < 10:
        raise HTTPException(status_code=422, detail="Please write a few more details so we can help.")
    subject = req.subject if req.subject in CONTACT_SUBJECTS else "Other"

    msg = db_models.ContactMessage(
        first_name=req.first_name.strip()[:100], last_name=req.last_name.strip()[:100],
        email=req.email.strip()[:200], subject=subject, message=req.message.strip()[:5000],
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    # Notify the inbox — never let a mail failure break the submission (it's already stored).
    host, smtp_user, smtp_pass = os.getenv("SMTP_HOST"), os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
    if host and smtp_user and smtp_pass:
        try:
            import smtplib
            from email.message import EmailMessage
            m = EmailMessage()
            m["Subject"] = f"[Mentor Visa contact] {subject} — {msg.first_name} {msg.last_name}"
            m["From"] = smtp_user
            m["To"] = os.getenv("CONTACT_NOTIFY_EMAIL", "contact@mentorvisa.com")
            m["Reply-To"] = msg.email
            m.set_content(f"From:    {msg.first_name} {msg.last_name} <{msg.email}>\n"
                          f"Subject: {subject}\nMessage #{msg.id}\n\n{msg.message}")
            with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=20) as s:
                s.starttls()
                s.login(smtp_user, smtp_pass)
                s.send_message(m)
        except Exception as e:
            print(f"[CONTACT] Email notify failed for message #{msg.id}: {e}")
    else:
        print(f"[CONTACT] New message #{msg.id} ({subject}) from {msg.email} — SMTP unset, stored only.")
    return {"status": "ok", "id": msg.id}


# ── GCMS Notes Orders ─────────────────────────────────────────────────────────
# Flow: create order (step 1 info) -> Stripe payment (pass_type='gcms') -> signed
# consent (IMM 5744) upload. Fulfilled manually via an ATIP request; a notification
# email goes out when an order is complete (paid + consent received).

GCMS_CONSENT_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}


def _gcms_order_dict(o: db_models.GCMSOrder) -> dict:
    return {
        "id": o.id,
        "status": o.status,
        "full_name": o.full_name,
        "family_name": o.family_name,
        "given_name": o.given_name,
        "related_persons": o.related_persons or [],
        "email": o.email,
        "date_of_birth": o.date_of_birth,
        "country_of_residence": o.country_of_residence,
        "uci": o.uci,
        "application_number": o.application_number,
        "application_type": o.application_type,
        "notes_type": o.notes_type,
        "extra_notes": o.extra_notes,
        "has_consent": bool(o.consent_file_id),
        "id_count": len(o.id_files or []),
        "created_at": o.timestamp_utc.isoformat() if o.timestamp_utc else None,
    }


def _send_gcms_order_email(order: db_models.GCMSOrder, consent_url: str | None = None,
                           id_urls: list | None = None):
    """Notify the fulfillment inbox that a GCMS order is complete (paid + documents uploaded).
    Uses SMTP env vars (SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS); logs and skips if unset
    so a missing mail setup never breaks the order flow."""
    host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    notify_to = os.getenv("GCMS_NOTIFY_EMAIL", "info@mentorvisa.com")
    if not (host and smtp_user and smtp_pass):
        print(f"[GCMS] SMTP not configured — order #{order.id} complete; NOT emailed. "
              f"Set SMTP_HOST/SMTP_USER/SMTP_PASS to enable notifications.")
        return
    try:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"] = f"[Mentor Visa] GCMS order #{order.id} ready to file — {order.full_name}"
        msg["From"] = smtp_user
        msg["To"] = notify_to
        lines = [
            f"GCMS notes order #{order.id} is PAID and the signed consent form has been uploaded.",
            "",
            f"Applicant:            {order.full_name}",
            f"Email:                {order.email}",
            f"Date of birth:        {order.date_of_birth}",
            f"Country of residence: {order.country_of_residence or '-'}",
            f"UCI:                  {order.uci or '-'}",
            f"Application number:   {order.application_number or '-'}",
            f"Application type:     {order.application_type or '-'}",
            f"Notes type:           {(order.notes_type or 'ircc').upper()}",
            f"Extra notes:          {order.extra_notes or '-'}",
        ]
        for i, p in enumerate(order.related_persons or [], 1):
            lines.append(f"Related person {i}:    {p.get('given_name','')} {p.get('family_name','')} "
                         f"({p.get('date_of_birth','')}, {p.get('relationship','') or '?'}"
                         f"{', under 16' if p.get('under_16') else ''})")
        lines += [
            "",
            f"Consent form (7-day link): {consent_url or 'stored as ' + (order.consent_file_id or '?')}",
        ]
        for label, url in (id_urls or []):
            lines.append(f"Government ID — {label or 'person'}: {url}")
        msg.set_content("\n".join(lines))
        port = int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        print(f"[GCMS] Notification email sent for order #{order.id}")
    except Exception as e:
        print(f"[GCMS] Failed to send notification email for order #{order.id}: {e}")


def _send_gcms_customer_email(order: db_models.GCMSOrder):
    """Confirmation to the customer once their documents are in. Same SMTP setup as the
    fulfillment email; logs and skips quietly if SMTP is not configured."""
    host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    if not (host and smtp_user and smtp_pass):
        print(f"[GCMS] SMTP not configured — customer confirmation for order #{order.id} NOT sent.")
        return
    try:
        import smtplib
        from email.message import EmailMessage
        first_name = (order.given_name or order.full_name or "there").split(" ")[0]
        n_people = 1 + len(order.related_persons or [])
        msg = EmailMessage()
        msg["Subject"] = f"We've received everything — your GCMS notes request is being filed (order #{order.id})"
        msg["From"] = f"Mentor Visa <{smtp_user}>"
        msg["To"] = order.email
        msg["Reply-To"] = "info@mentorvisa.com"
        msg.set_content(f"""Hi {first_name},

Good news — your signed consent form and identity document{'s' if n_people > 1 else ''} for {n_people} person{'s' if n_people > 1 else ''} arrived safely, and your GCMS notes order is complete.

What happens next:

  1. We verify your documents and file your ATIP request with IRCC within 1 business day.
  2. IRCC typically releases GCMS notes within 30-40 days.
  3. The day your notes arrive, we email the complete file to this address.

You can check your order status any time at https://mentorvisa.com/order-gcms-notes

While you wait, most applicants find these useful:
  - How to read your GCMS notes: https://mentorvisa.com/how-to-read-gcms-notes
  - Track your application milestones: https://mentorvisa.com/track-my-application

A note on your documents: your signed form and ID are stored encrypted, used only to file this request with IRCC, never shared with anyone else, and retained no longer than required for record-keeping (maximum 6 years).

Questions? Just reply to this email.

Mentor Visa
info@mentorvisa.com · mentorvisa.com""")
        port = int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        print(f"[GCMS] Customer confirmation sent for order #{order.id} to {order.email}")
    except Exception as e:
        print(f"[GCMS] Failed to send customer confirmation for order #{order.id}: {e}")


class GCMSOrderRequest(BaseModel):
    family_name: str = ""         # surname, as on passport
    given_name: str = ""          # given name(s), as on passport
    full_name: str = ""           # legacy — derived from the two above when absent
    email: str
    date_of_birth: str            # YYYY-MM-DD
    country_of_residence: Optional[str] = None
    uci: Optional[str] = None
    application_number: Optional[str] = None
    application_type: Optional[str] = None
    notes_type: str = "ircc"      # 'ircc' or 'cbsa'
    extra_notes: Optional[str] = None


@app.post("/api/v1/gcms/orders")
def create_gcms_order(
    req: GCMSOrderRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """Step 1 — save the applicant info and open an order awaiting payment."""
    ensure_user_exists(user_id, db)
    # Derive the legacy full_name from the split fields (or split a legacy full_name).
    if req.family_name.strip() or req.given_name.strip():
        req.full_name = f"{req.given_name.strip()} {req.family_name.strip()}".strip()
    elif req.full_name.strip():
        parts = req.full_name.strip().rsplit(" ", 1)
        req.given_name, req.family_name = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
    if not req.full_name.strip() or not req.email.strip() or "@" not in req.email:
        raise HTTPException(status_code=422, detail="Please provide your name and a valid email.")
    if not req.date_of_birth.strip():
        raise HTTPException(status_code=422, detail="Please provide your date of birth.")
    if not (req.application_number or "").strip():
        raise HTTPException(status_code=422, detail="Please provide your application number.")
    if req.notes_type not in ("ircc", "cbsa"):
        raise HTTPException(status_code=422, detail="notes_type must be 'ircc' or 'cbsa'.")

    # Reuse an identical unpaid order instead of stacking duplicates (double-clicks, refreshes).
    order = (db.query(db_models.GCMSOrder)
             .filter_by(user_id=user_id, status='awaiting_payment')
             .order_by(db_models.GCMSOrder.id.desc()).first())
    if order:
        for k, v in req.model_dump().items():
            setattr(order, k, v.strip() if isinstance(v, str) else v)
    else:
        order = db_models.GCMSOrder(user_id=user_id, status='awaiting_payment',
                                    **{k: (v.strip() if isinstance(v, str) else v) for k, v in req.model_dump().items()})
        db.add(order)

    # Prepaid GCMS credit (granted manually / promotions): consume it and skip the payment step.
    user = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
    if user and (user.gcms_credits or 0) > 0:
        user.gcms_credits -= 1
        order.status = 'awaiting_consent'
        user.gcms_analyzer_credits = (user.gcms_analyzer_credits or 0) + 1  # analysis included with every order
        print(f"[GCMS] Consumed 1 prepaid credit for {user_id} (order pre-paid; {user.gcms_credits} left)")

    db.commit()
    db.refresh(order)
    return _gcms_order_dict(order)


@app.put("/api/v1/gcms/orders/{order_id}")
def update_gcms_order(
    order_id: int,
    req: GCMSOrderRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """Edit applicant details on an existing order — including after payment, as long as
    the signed documents haven't been submitted yet (they embed the old details)."""
    order = db.query(db_models.GCMSOrder).filter_by(id=order_id, user_id=user_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    if order.status not in ('awaiting_payment', 'awaiting_consent'):
        raise HTTPException(status_code=409,
                            detail="Documents were already submitted for this order. "
                                   "Email info@mentorvisa.com and we'll correct the details before filing.")
    if req.family_name.strip() or req.given_name.strip():
        req.full_name = f"{req.given_name.strip()} {req.family_name.strip()}".strip()
    if not req.full_name.strip() or not req.email.strip() or "@" not in req.email:
        raise HTTPException(status_code=422, detail="Please provide your name and a valid email.")
    if not req.date_of_birth.strip():
        raise HTTPException(status_code=422, detail="Please provide your date of birth.")
    if not (req.application_number or "").strip():
        raise HTTPException(status_code=422, detail="Please provide your application number.")
    for k, v in req.model_dump().items():
        setattr(order, k, v.strip() if isinstance(v, str) else v)
    db.commit()
    db.refresh(order)
    return _gcms_order_dict(order)


@app.get("/api/v1/gcms/orders")
def list_gcms_orders(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """List the caller's GCMS orders (newest first). Lazily verifies payment with Stripe for
    orders still awaiting payment, so a delayed/failed webhook can't strand a paid order."""
    orders = (db.query(db_models.GCMSOrder).filter_by(user_id=user_id)
              .order_by(db_models.GCMSOrder.id.desc()).all())
    changed = False
    for o in orders:
        if o.status == 'awaiting_payment' and o.stripe_session_id:
            try:
                session = stripe.checkout.Session.retrieve(o.stripe_session_id)
                if getattr(session, "payment_status", None) == 'paid':
                    o.status = 'awaiting_consent'
                    ua_o = db.query(db_models.UserAccount).filter_by(user_id=o.user_id).first()
                    if ua_o:
                        ua_o.gcms_analyzer_credits = (ua_o.gcms_analyzer_credits or 0) + 1  # included analysis
                    changed = True
            except Exception as e:
                print(f"[GCMS] Stripe verify failed for order #{o.id}: {e}")
        # Documents in hand for 24h -> the ATIP request has been filed (our 1-business-day SLA).
        elif (o.status == 'received' and o.received_at
              and (datetime.datetime.utcnow() - o.received_at).total_seconds() > 24 * 3600):
            o.status = 'filed'
            changed = True
    if changed:
        db.commit()
    return {"orders": [_gcms_order_dict(o) for o in orders]}


async def _store_gcms_upload(upload: UploadFile, stored_name_prefix: str, order_id: int) -> tuple:
    """Validate + store one GCMS document (Supabase bucket or local fallback).
    Returns (stored_name, signed_url_or_None)."""
    ext = os.path.splitext(upload.filename or "")[1].lower()
    if ext not in GCMS_CONSENT_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"'{upload.filename}': please upload PDF, JPG, or PNG files.")
    content = await upload.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=422, detail=f"'{upload.filename}' is too large (max 10MB).")
    stored_name = f"{stored_name_prefix}_{order_id}_{uuid.uuid4().hex[:8]}{ext}"
    url = None
    if supabase:
        supabase.storage.from_("documents").upload(
            stored_name, content,
            {"content-type": upload.content_type or "application/octet-stream"},
        )
        try:
            signed = supabase.storage.from_("documents").create_signed_url(stored_name, 7 * 24 * 3600)
            url = signed.get("signedURL") or signed.get("signedUrl")
        except Exception as e:
            print(f"[GCMS] Could not create signed URL for {stored_name}: {e}")
    else:
        with open(UPLOADS_DIR / stored_name, "wb") as f:
            f.write(content)
    return stored_name, url


@app.post("/api/v1/gcms/orders/{order_id}/consent")
async def upload_gcms_consent(
    order_id: int,
    consent: UploadFile = File(...),
    ids: list[UploadFile] = File(default=[]),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """Step 3 — store the signed IMM 5744 plus one government-issued ID per person
    (IRCC requires identity proof to process an ATIP request), then mark the order complete."""
    order = db.query(db_models.GCMSOrder).filter_by(id=order_id, user_id=user_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    if order.status == 'awaiting_payment':
        raise HTTPException(status_code=402, detail="Please complete payment before uploading documents.")

    expected_ids = 1 + len(order.related_persons or [])
    if len(ids) < expected_ids:
        raise HTTPException(status_code=422,
                            detail=f"Please attach a government-issued ID for each of the {expected_ids} "
                                   "person(s) on the application.")

    consent_name, consent_url = await _store_gcms_upload(consent, "gcms_consent", order.id)

    # Label each ID by person (applicant first, then related in order).
    person_labels = [f"{order.given_name or ''} {order.family_name or order.full_name}".strip()]
    person_labels += [f"{p.get('given_name','')} {p.get('family_name','')}".strip()
                      for p in (order.related_persons or [])]
    id_records, id_urls = [], []
    for i, f in enumerate(ids):
        label = person_labels[i] if i < len(person_labels) else f"Person {i + 1}"
        stored, url = await _store_gcms_upload(f, "gcms_id", order.id)
        id_records.append({"person": label, "stored_name": stored})
        id_urls.append((label, url or stored))

    order.consent_file_id = consent_name
    order.id_files = id_records
    if order.status in ('awaiting_consent', 'received'):
        order.status = 'received'
        order.received_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(order)

    _send_gcms_order_email(order, consent_url, id_urls)
    _send_gcms_customer_email(order)
    return _gcms_order_dict(order)


class GCMSPersonsRequest(BaseModel):
    # [{family_name, given_name, date_of_birth, relationship, under_16}]
    persons: list = []


@app.put("/api/v1/gcms/orders/{order_id}/persons")
def set_gcms_persons(
    order_id: int,
    req: GCMSPersonsRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """Step 3a — record the other people included on the application (IMM 5744 sec. 2.1-2.3)."""
    import gcms_form
    order = db.query(db_models.GCMSOrder).filter_by(id=order_id, user_id=user_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    if len(req.persons) > gcms_form.MAX_RELATED:
        raise HTTPException(status_code=422,
                            detail=f"IMM 5744 fits the applicant plus {gcms_form.MAX_RELATED} people. "
                                   "Add the rest in the notes and we'll prepare a second form.")
    cleaned = []
    for p in req.persons:
        fam = (p.get("family_name") or "").strip()
        giv = (p.get("given_name") or "").strip()
        dob = (p.get("date_of_birth") or "").strip()
        if not (fam and giv and dob):
            raise HTTPException(status_code=422, detail="Each person needs a surname, given name(s), and date of birth.")
        cleaned.append({
            "family_name": fam, "given_name": giv, "date_of_birth": dob,
            "relationship": (p.get("relationship") or "").strip(),
            "under_16": bool(p.get("under_16")),
        })
    order.related_persons = cleaned
    db.commit()
    db.refresh(order)
    return _gcms_order_dict(order)


@app.get("/api/v1/gcms/orders/{order_id}/consent-form")
def download_gcms_consent_form(
    order_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """Step 3b — the pre-filled, flattened IMM 5744 ready to print, sign in blue ink, and scan."""
    import gcms_form
    from fastapi.responses import Response
    order = db.query(db_models.GCMSOrder).filter_by(id=order_id, user_id=user_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    if order.status == 'awaiting_payment':
        raise HTTPException(status_code=402, detail="Please complete payment first.")
    try:
        pdf_bytes, form_code = gcms_form.fill_consent_form(order)
    except Exception as e:
        print(f"[GCMS] Consent form generation failed for order #{order.id}: {e}")
        raise HTTPException(status_code=500, detail="Could not generate the consent form. Please try again.")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{form_code}_prefilled_order{order.id}.pdf"'},
    )


# ── GCMS Notes AI Analyzer ─────────────────────────────────────────────────────
# Pay-first: the analysis endpoint refuses to touch the PDF until the user holds a
# gcms_analyzer_credit (granted by a $14.90 purchase, or included with every GCMS order).

@app.get("/api/v1/gcms-analysis/status")
def gcms_analysis_status(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """Credits + past analyses for the analyzer page. Also lazily verifies any initiated
    analyzer checkout with Stripe (same pattern as GCMS orders — works without the webhook)."""
    ensure_user_exists(user_id, db)
    ua = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
    pending = db.query(db_models.PaymentEvent).filter_by(
        user_id=user_id, pass_type='gcms_analyzer', event_type='checkout_initiated').all()
    for pe in pending:
        try:
            session = stripe.checkout.Session.retrieve(pe.stripe_session_id)
            if getattr(session, "payment_status", None) == 'paid':
                pe.event_type = 'checkout_success'
                ua.gcms_analyzer_credits = (ua.gcms_analyzer_credits or 0) + 1
                print(f"[GCMS-Analyzer] Lazily verified paid session for {user_id}")
        except Exception as e:
            print(f"[GCMS-Analyzer] Stripe verify failed: {e}")
    db.commit()

    analyses = (db.query(db_models.Evaluation)
                .filter_by(user_id=user_id, evaluation_type='gcms_analysis')
                .order_by(db_models.Evaluation.id.desc()).limit(20).all())
    return {
        "credits": (ua.gcms_analyzer_credits or 0) if ua else 0,
        "analyses": [{
            "stored_file_id": a.stored_file_id,
            "original_filename": a.original_filename,
            "created_at": a.timestamp_toronto.isoformat() if a.timestamp_toronto else None,
            "result": {k: v for k, v in (a.payload or {}).items() if k != "_extracted_text"},
        } for a in analyses],
    }


@app.post("/api/v1/gcms-analysis")
@limiter.limit("10/hour")
async def analyze_gcms_notes(
    request: Request,
    document: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """Analyze an uploaded GCMS/ATIP notes PDF. STRICTLY pay-first: no extraction or LLM work
    happens without a credit. The credit is only consumed when the document is a valid notes
    release (a wrong upload costs nothing — the user can retry)."""
    ensure_user_exists(user_id, db)
    ua = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
    if not ua or (ua.gcms_analyzer_credits or 0) <= 0:
        raise HTTPException(status_code=402, detail="This analysis requires a purchase. "
                            "Buy an analysis report (or place a GCMS order — it includes one free).")

    filename = document.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext != '.pdf':
        raise HTTPException(status_code=400, detail="Please upload the PDF you received from IRCC/CBSA.")
    doc_bytes = await document.read()
    if len(doc_bytes) > 20 * 1024 * 1024:  # ATIP releases can be big — 20MB cap for this endpoint only
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 20MB.")

    import gcms_analyzer
    try:
        report = gcms_analyzer.run_gcms_analysis(doc_bytes, ai_service.openai_client)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        print(f"[GCMS-Analyzer] Analysis failed for {user_id}: {e}")
        raise HTTPException(status_code=500, detail="The analysis failed. Your credit was NOT used — please try again.")

    if not report.get("document_valid", False):
        # Wrong document — do not consume the credit, return the rejection so the user can retry.
        return {**report, "credit_consumed": False, "credits_remaining": ua.gcms_analyzer_credits or 0}

    ua.gcms_analyzer_credits -= 1

    file_id = str(uuid.uuid4())
    stored_filename = f"{file_id}.pdf"
    try:
        if supabase:
            supabase.storage.from_("documents").upload(
                path=stored_filename, file=doc_bytes, file_options={"content-type": "application/pdf"})
        else:
            with open(UPLOADS_DIR / stored_filename, "wb") as f:
                f.write(doc_bytes)
    except Exception as e:
        print(f"[GCMS-Analyzer] File storage failed (analysis still returned): {e}")

    report["stored_file_id"] = file_id
    report["original_filename"] = filename
    record = db_models.Evaluation(
        evaluation_type='gcms_analysis',
        user_id=user_id,
        document_type="GCMS Notes Analysis",
        role_name=None,
        company_name=None,
        original_filename=filename,
        stored_file_id=file_id,
        compliance_status="N/A",
        is_premium_unlocked=1,
        payload=report,
    )
    db.add(record)
    db.commit()

    return {**report, "credit_consumed": True, "credits_remaining": ua.gcms_analyzer_credits or 0}


class UnlockRequest(BaseModel):
    file_id: str
    pass_type: str # 'finder' or 'auditor'

@app.post("/api/v1/unlock-evaluation")
def unlock_evaluation(
    req: UnlockRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db)
):
    """Consume a user's credit to permanently unlock an evaluation result."""
    ensure_user_exists(user_id, db)
    user = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
    if not user:
        raise HTTPException(status_code=403, detail="No credits available. Please purchase a pass.")
        
    if req.pass_type == 'auditor':
        # Starter and Complete tiers get unlimited audits
        has_tier_access = user.subscription_tier in ('starter', 'complete')
        if not has_tier_access:
            if user.audit_letter_credits <= 0:
                raise HTTPException(status_code=403, detail="No audit credits available. Please upgrade to Starter or purchase a credit.")
            user.audit_letter_credits -= 1
        # Tier users: no credit deduction, just unlock
    elif req.pass_type == 'letter_builder':
        # Complete tier gets unlimited letter builder
        has_tier_access = user.subscription_tier == 'complete'
        if not has_tier_access:
            if user.letter_builder_credits <= 0:
                raise HTTPException(status_code=403, detail="No letter builder credits available. Please upgrade to Complete or purchase a credit.")
            user.letter_builder_credits -= 1
    else:
        if user.find_noc_credits <= 0:
            raise HTTPException(status_code=403, detail="No finder credits available.")
        user.find_noc_credits -= 1

    if req.pass_type == 'auditor':
        records = db.query(db_models.Evaluation).filter_by(evaluation_type='audit', stored_file_id=req.file_id).all()
    else:
        records = db.query(db_models.Evaluation).filter_by(evaluation_type='noc_finder', stored_file_id=req.file_id).all()

    if not records:
        db.rollback()
        raise HTTPException(status_code=404, detail="Evaluation record not found.")

    for record in records:
        # Tie this record permanently to the user if it was anonymous
        if record.user_id == "anonymous":
            record.user_id = user_id
        record.is_premium_unlocked = 1
        
    db.commit()
    
    return {"status": "unlocked", "remaining_finder": user.find_noc_credits, "remaining_auditor": user.audit_letter_credits}


class CancelRequest(BaseModel):
    session_id: str

@app.post("/api/v1/payment-events/cancel")
def cancel_payment_event(req: CancelRequest, db: Session = Depends(database.get_db)):
    """Marks a payment event as canceled gracefully for tracking purposes"""
    pe = db.query(db_models.PaymentEvent).filter_by(stripe_session_id=req.session_id).first()
    if pe and pe.event_type == 'checkout_initiated':
        pe.event_type = 'checkout_returned_unpaid'
        db.commit()
    return {"status": "ok"}


# --- ITA Strategy Report Endpoints ---

class ITAStrategyRequest(BaseModel):
    evaluation_id: int

@app.post("/api/v1/ita-strategy/generate")
def generate_ita_strategy_endpoint(
    req: ITAStrategyRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db)
):
    """RETIRED: the ITA Strategy report is a legacy product and is no longer offered."""
    raise HTTPException(status_code=410, detail="The ITA Strategy report has been retired.")
    ensure_user_exists(user_id, db)

    # Check credits
    user = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
    if not user or user.ita_strategy_credits < 1:
        raise HTTPException(status_code=402, detail="No ITA Strategy credits remaining. Please purchase a credit.")
    
    # Fetch the source CRS evaluation
    evaluation = db.query(db_models.Evaluation).filter_by(id=req.evaluation_id, user_id=user_id, evaluation_type='crs_calculator').first()
    if not evaluation:
        raise HTTPException(status_code=404, detail="CRS evaluation not found.")
    
    payload = evaluation.payload or {}
    raw_inputs = payload.get('raw_inputs', {})
    score_data = payload.get('score', {})
    breakdown_data = payload.get('breakdown', {})
    
    if not raw_inputs or not score_data:
        raise HTTPException(status_code=400, detail="CRS evaluation is missing required data.")
    
    try:
        # Generate the strategy via AI
        strategy_report = ai_service.generate_ita_strategy(raw_inputs, score_data, breakdown_data)
        
        # Consume 1 credit
        user.ita_strategy_credits -= 1
        
        # Store the strategy as a new evaluation record linked to the source
        strategy_record = db_models.Evaluation(
            evaluation_type='ita_strategy',
            user_id=user_id,
            document_type='ITA Strategy Report',
            role_name=f"CRS Score: {score_data.get('total', 'N/A')}",
            company_name='Express Entry',
            compliance_status='Generated',
            is_premium_unlocked=1,
            payload={
                'evaluation_type': 'ita_strategy',
                'source_evaluation_id': req.evaluation_id,
                'source_score': score_data,
                'source_raw_inputs': raw_inputs,
                'strategy': strategy_report,
            }
        )
        db.add(strategy_record)
        db.commit()
        db.refresh(strategy_record)
        
        return {
            "success": True,
            "strategy_id": strategy_record.id, 
            "strategy": strategy_report,
            "remaining_credits": user.ita_strategy_credits
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Strategy generation failed: {str(e)}")


@app.get("/api/v1/ita-strategy/{evaluation_id}")
def get_ita_strategy(
    evaluation_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db)
):
    """RETIRED: the ITA Strategy report is a legacy product and is no longer offered."""
    raise HTTPException(status_code=410, detail="The ITA Strategy report has been retired.")
    # Look for ita_strategy records that reference this source evaluation
    strategies = db.query(db_models.Evaluation).filter_by(
        user_id=user_id,
        evaluation_type='ita_strategy'
    ).order_by(db_models.Evaluation.id.desc()).all()
    
    # Find the one linked to this evaluation_id
    for s in strategies:
        payload = s.payload or {}
        if payload.get('source_evaluation_id') == evaluation_id:
            return {
                "success": True,
                "strategy_id": s.id,
                "strategy": payload.get('strategy', {}),
                "generated_at": str(s.timestamp_toronto)
            }
    
    return {"success": False, "strategy": None}


# --- Profile Builder Agent Endpoints ---

import profile_builder_service
from profile_builder_models import ChatRequest, Conversation
from fastapi.responses import StreamingResponse
import asyncio
import base64

# Ensure conversations table exists
Conversation.__table__.create(bind=database.engine, checkfirst=True)


@app.post("/api/v1/profile-builder/chat")
@limiter.limit("120/hour")
async def profile_builder_chat(
    request: Request,
    req: ChatRequest,
    user_id: str = Depends(get_current_user_optional),
    db: Session = Depends(database.get_db)
):
    """Stream a chat response from the Profile Builder AI agent.
    
    Graduated access:
    - Anonymous: 2 questions/day (IP rate limited), no conversation save
    - Free tier: 5 credits (granted on sign-up)
    - Starter tier: 20 credits (granted on purchase)
    - Complete tier: Unlimited
    
    Returns Server-Sent Events (SSE) stream.
    """
    is_anonymous = (user_id == "anonymous")
    user = None
    credits_after = 0
    
    if is_anonymous:
        # Anonymous: IP rate limit handles gating (2/day set above via limiter)
        # No credit deduction, no DB save
        credits_after = 0  # Signals "sign in for more" to frontend
    else:
        ensure_user_exists(user_id, db)
        user = db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
        
        if user and user.subscription_tier == 'complete':
            # Complete tier: unlimited
            credits_after = -1  # -1 signals "unlimited" to frontend
        elif user and user.profile_builder_credits > 0:
            # Free or Starter tier with credits remaining: deduct 1
            user.profile_builder_credits -= 1
            credits_after = user.profile_builder_credits
            db.commit()
        else:
            # No credits remaining — tell frontend which tier to upgrade to
            current_tier = user.subscription_tier if user else 'free'
            upgrade_to = 'starter' if current_tier == 'free' else 'complete'
            raise HTTPException(
                status_code=403,
                detail=json.dumps({
                    "code": "credits_exhausted",
                    "current_tier": current_tier,
                    "upgrade_to": upgrade_to,
                    "message": f"No AI Assistant credits remaining. Upgrade to {upgrade_to} for more."
                })
            )
    
    # 4. Handle image in latest message (upload to Supabase Storage)
    image_data = None
    image_mime = None
    latest_msg = req.messages[-1] if req.messages else None
    image_url_for_storage = None
    
    if latest_msg and latest_msg.image_data:
        try:
            # Decode base64 image
            # Handle data URL format: "data:image/png;base64,xxxxx"
            b64_str = latest_msg.image_data
            if "," in b64_str:
                header, b64_str = b64_str.split(",", 1)
                # Extract MIME from header like "data:image/png;base64"
                image_mime = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
            else:
                image_mime = "image/png"
            
            image_data = base64.b64decode(b64_str)
            
            # Upload to Supabase Storage (or save locally)
            ext_map = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
            ext = ext_map.get(image_mime, ".png")
            img_file_id = str(uuid.uuid4())
            stored_name = f"chat-images/{img_file_id}{ext}"
            
            if supabase:
                supabase.storage.from_("documents").upload(
                    path=stored_name,
                    file=image_data,
                    file_options={"content-type": image_mime}
                )
                # Get a signed URL for storage reference
                image_url_for_storage = stored_name
            else:
                # Local dev: save to uploads dir
                chat_img_dir = UPLOADS_DIR / "chat-images"
                chat_img_dir.mkdir(exist_ok=True)
                with open(chat_img_dir / f"{img_file_id}{ext}", "wb") as f:
                    f.write(image_data)
                image_url_for_storage = stored_name
                
        except Exception as img_err:
            print(f"Warning: failed to process chat image: {img_err}")
            image_data = None
            image_mime = None
    
    # 5. Build user context from journey data (skip for anonymous)
    user_context = ""
    if not is_anonymous:
        journey = db.query(journey_models.PRJourney).filter_by(user_id=user_id).first()
        journey_dict = {}
        profile_dict = {}
        if journey:
            journey_dict = {
                "noc_code": journey.noc_code,
                "noc_title": journey.noc_title,
                "teer_category": journey.teer_category,
                "crs_score": journey.crs_score,
                "crs_calculated_at": journey.crs_calculated_at.isoformat() if journey.crs_calculated_at else None,
                "eligible_programs": journey.eligible_programs,
            }
            profile_dict = journey.profile_data or {}
        user_context = profile_builder_service.build_user_context(journey_dict, profile_dict)
    
    # 6. Prepare messages for LLM (prune history)
    msg_dicts = [{"role": m.role, "content": m.content, "image_url": m.image_data} for m in req.messages]
    # Replace latest message's image_data with the storage URL
    if image_url_for_storage and msg_dicts:
        msg_dicts[-1]["image_url"] = image_url_for_storage
    
    pruned = profile_builder_service.prepare_messages_for_llm(msg_dicts)
    
    # 7. Stream response
    conversation_id = req.conversation_id or str(uuid.uuid4())
    assistant_content = []  # Accumulate for persistence
    credit_refunded = False
    
    async def event_stream():
        nonlocal credit_refunded
        try:
            first_chunk = True
            async for chunk in profile_builder_service.stream_chat_response(
                messages=pruned,
                user_context=user_context,
                image_data=image_data,
                image_mime=image_mime,
            ):
                first_chunk = False
                assistant_content.append(chunk)
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
            
            # Send completion event with metadata
            yield f"data: {json.dumps({'type': 'done', 'conversation_id': conversation_id, 'credits_remaining': credits_after})}\n\n"
            
        except Exception as stream_err:
            print(f"Profile Builder stream error: {stream_err}")
            # Refund credit if we deducted one (non-anonymous, non-complete)
            if not is_anonymous and user and user.subscription_tier != 'complete':
                try:
                    refund_db = database.SessionLocal()
                    refund_user = refund_db.query(db_models.UserAccount).filter_by(user_id=user_id).first()
                    if refund_user:
                        refund_user.profile_builder_credits += 1
                        refund_db.commit()
                        credit_refunded = True
                    refund_db.close()
                except Exception as refund_err:
                    print(f"Warning: failed to refund credit: {refund_err}")
            
            yield f"data: {json.dumps({'type': 'error', 'message': str(stream_err), 'credit_refunded': credit_refunded})}\n\n"
        finally:
            # Save conversation to DB (skip for anonymous users)
            if is_anonymous:
                return
            try:
                save_db = database.SessionLocal()
                full_assistant_text = "".join(assistant_content)
                
                # Build the message list for storage (no base64 — only URLs)
                stored_messages = []
                for m in req.messages:
                    stored_msg = {"role": m.role, "content": m.content}
                    if m == latest_msg and image_url_for_storage:
                        stored_msg["image_url"] = image_url_for_storage
                    stored_messages.append(stored_msg)
                
                # Add assistant response
                if full_assistant_text:
                    stored_messages.append({"role": "assistant", "content": full_assistant_text})
                
                # Upsert conversation
                existing = save_db.query(Conversation).filter_by(conversation_id=conversation_id).first()
                if existing:
                    existing.messages = stored_messages
                    existing.updated_at = datetime.datetime.utcnow()
                else:
                    # Auto-title from first user message
                    first_user_msg = next((m.content for m in req.messages if m.role == "user"), "New conversation")
                    title = first_user_msg[:80] + ("..." if len(first_user_msg) > 80 else "")
                    
                    new_convo = Conversation(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        title=title,
                        messages=stored_messages,
                    )
                    save_db.add(new_convo)
                
                save_db.commit()
                save_db.close()
            except Exception as save_err:
                print(f"Warning: failed to save conversation: {save_err}")
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


@app.get("/api/v1/profile-builder/conversations")
def list_profile_builder_conversations(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db)
):
    """List the user's past Profile Builder conversations."""
    convos = (
        db.query(Conversation)
        .filter_by(user_id=user_id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )
    return {
        "conversations": [
            {
                "conversation_id": c.conversation_id,
                "title": c.title or "Untitled",
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in convos
        ]
    }


@app.get("/api/v1/profile-builder/conversations/{conversation_id}")
def get_profile_builder_conversation(
    conversation_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(database.get_db)
):
    """Load a specific conversation's full message history."""
    convo = db.query(Conversation).filter_by(
        conversation_id=conversation_id,
        user_id=user_id
    ).first()
    
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    return {
        "conversation_id": convo.conversation_id,
        "title": convo.title,
        "messages": convo.messages or [],
        "created_at": convo.created_at.isoformat() if convo.created_at else None,
        "updated_at": convo.updated_at.isoformat() if convo.updated_at else None,
    }
