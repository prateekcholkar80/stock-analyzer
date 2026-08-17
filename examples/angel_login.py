from app.angel.client import AngelOneClient


def main():

    angel_client = AngelOneClient()

    response = angel_client.login()

    print("Login successful")
    print(response["status"])


if __name__ == "__main__":
    main()
