from app.angel.client import AngelOneClient
from app.runtime import run_entrypoint


def main():
    angel_client = AngelOneClient()

    response = angel_client.login()

    print("Login successful")
    print(response["status"])


if __name__ == "__main__":
    raise SystemExit(
        run_entrypoint(
            main,
            logger_name="examples.angel_login",
        )
    )
