import asyncio

from services.hindsight_service import store_incident, search_incidents


async def main():
    print("Connecting to Hindsight...")

    incident = """
    Incident INC-002:

    The login API was returning HTTP 500 errors.

    Root cause:
    The application was unable to connect to the database.

    Fix:
    Restarted the database service and corrected the database connection configuration.

    Result:
    The login API started working normally.
    """

    # Store incident
    await store_incident(incident)

    print("Incident stored successfully!")

    # Search previous incidents
    results = await search_incidents(
        "Have we experienced database connection problems before?"
    )


    print("\nPrevious related incidents:")


    if results:

        for memory in results:
            print("------------------------------")
            print(memory)

    else:

        print("No related incidents found.")


if __name__ == "__main__":
    asyncio.run(main())