"""Purge expired conversations, checkpoints, and download references."""

import asyncio

from sqlalchemy import delete, select

from agents.coordinator.graph import coordinator
from core.memory.store import store
from mock_bank.database.session import SessionLocal
from mock_bank.models.entities import AgentCheckpoint, ChatMessage, ChatSession, Statement, now


async def main():
    with SessionLocal.begin() as db:
        expired = list(db.scalars(select(ChatSession).where(ChatSession.expires_at < now())))
        if expired:
            await coordinator.start()
        for chat in expired:
            assert coordinator.graph is not None
            await coordinator.graph.checkpointer.adelete_thread(chat.id)
            db.execute(delete(AgentCheckpoint).where(AgentCheckpoint.session_id == chat.id))
            db.execute(delete(ChatMessage).where(ChatMessage.session_id == chat.id))
            store.delete("chat-meta:" + chat.id)
            db.delete(chat)
        db.execute(delete(Statement).where(Statement.expires_at < now()))
    await coordinator.close()
    print("Expired conversations, checkpoints and statement references removed.")


if __name__ == "__main__":
    asyncio.run(main())
