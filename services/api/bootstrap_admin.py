import getpass

from pydantic import ValidationError

from .database import open_database
from .models.auth import Role, UserCreate
from .services.users import create_user


def main():
    email = input("Administrator email: ").strip()
    name = input("Administrator display name (optional): ").strip() or None
    password = getpass.getpass("Administrator password: ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match")
    try:
        payload = UserCreate(email=email, password=password, role=Role.admin, name=name)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise SystemExit(f"Invalid administrator details: {details}") from None
    database = open_database()
    try:
        if database.table("users").all():
            raise SystemExit("Bootstrap is only available when no users exist")
        create_user(database, payload)
    finally:
        database.close()
    print("Administrator created")


if __name__ == "__main__":
    import sys
    from json import JSONDecodeError

    try:
        main()
    except (OSError, EOFError, JSONDecodeError) as exc:
        print(f"Administrator setup failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
