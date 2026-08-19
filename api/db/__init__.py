"""
Database layer (Phase 3.2).

Three objects with three different lifetimes:

    Engine        one per application   owns the connection pool
    Sessionmaker  one per application   a factory, holds no state
    AsyncSession  one per REQUEST       a transaction

Confusing those lifetimes is the main source of async SQLAlchemy bugs.
Sharing one session between requests means sharing one transaction:
user A's uncommitted work becomes visible to user B, and A's rollback
destroys B's.
"""
