import requests


url = "http://127.0.0.1:5000/api/incidents"


incident = {
    "title": "API Latency Spike",
    "description": "API response time increased to 4.8 seconds and database connection requests are timing out.",
    "service": "Payment API",
    "severity": "Critical",
    "deployment": "v2.4.1"
}


response = requests.post(
    url,
    json=incident
)


print("Status:", response.status_code)
print("Response:")
print(response.json())