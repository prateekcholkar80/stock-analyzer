from app.angel.client import AngelOneClient
from app.logging_config import configure_logging


def main():
    configure_logging()

    angel_client = AngelOneClient()

    response = angel_client.login()

    print("Login successful")
    print(response["status"])


if __name__ == "__main__":
    main()
