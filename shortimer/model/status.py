""""""

from enum import StrEnum


class Role(StrEnum):
    """What a user may do *globally*.

    This axis answers "what may you see across the whole deployment?" and
    nothing else. It is deliberately not the place a gym owner goes: being
    privileged over your own gym's data is a question of *scope* — which
    records — not of rank, and folding the two together produces a role enum
    that has to grow a new member every time a new kind of boundary appears.
    Gym scoping belongs with the plan/tier work; see `docs/roadmap.md`.
    """

    #: Everyone. Sees their own data and nothing else.
    USER = "user"
    #: Support and ops: may read the operator metrics, may not administer
    #: accounts. Exists so the privileged check can't collapse to `is_admin`.
    STAFF = "staff"
    #: The operator. Everything, including invites and other accounts.
    ADMIN = "admin"


#: Roles allowed to read the operator metrics — global spend, every user's
#: activity. Named here rather than spelled out at the call site so that
#: adding a privileged surface is a change in one place.
OPERATOR_ROLES = frozenset({Role.STAFF, Role.ADMIN})


class AccountStatus(StrEnum):
    """Whether an account may be used at all, separate from what it may do."""

    ACTIVE = "active"
    #: Sign-in refused, data retained. What a ban or a voluntary pause looks
    #: like; deletion is a different operation.
    DISABLED = "disabled"
