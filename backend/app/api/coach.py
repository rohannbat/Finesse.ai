import anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.schemas import CoachChatRequest, CoachChatResponse, CoachHistoryResponse
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import CoachMessage, User
from app.services import coach

router = APIRouter(prefix="/api/coach", tags=["coach"])


@router.post("/chat", response_model=CoachChatResponse)
def chat(
    body: CoachChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ask the AI coach a question. Claude answers grounded in the user's
    current WHOOP + nutrition data and the recent conversation history."""
    try:
        reply = coach.chat(db, user, body.message)
    except (anthropic.AnthropicError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=f"Coach unavailable: {exc}")
    return CoachChatResponse(reply=reply)


@router.get("/history", response_model=CoachHistoryResponse)
def history(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    messages = db.scalars(
        select(CoachMessage)
        .where(CoachMessage.user_id == user.id)
        .order_by(CoachMessage.created_at.desc())
        .limit(min(limit, 200))
    ).all()
    return CoachHistoryResponse(messages=list(reversed(messages)))


@router.delete("/history", status_code=204)
def clear_history(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.execute(delete(CoachMessage).where(CoachMessage.user_id == user.id))
    db.commit()
