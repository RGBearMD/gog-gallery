import requests

URL = "https://www.gog.com/account/getFilteredProducts"

params = {
    "mediaType": 1,
    "sortBy": "title",
    "system": "",
    "page": 1
}

r = requests.get(URL, params=params)

print("STATUS:", r.status_code)
print("CONTENT-TYPE:", r.headers.get("content-type"))
print("TEXT START:\n", r.text[:500])