import requests
from bs4 import BeautifulSoup

url = "https://www.mcxindia.com/market-data/bhavcopy"
s = requests.Session()

# 1) Get the page to collect cookies and hidden ASP.NET fields
r = s.get(url, headers={"User-Agent": "Mozilla/5.0"})
soup = BeautifulSoup(r.text, "html.parser")

data = {}
for name in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"]:
    el = soup.find("input", {"name": name})
    if el and el.get("value"):
        data[name] = el["value"]

# 2) Set the postback target for the CSV link
data["__EVENTTARGET"] = "ctl00$cph_InnerContainerRight$C001$lnkExpToCSV"
data["__EVENTARGUMENT"] = ""

# 3) Submit the form back to the same page
resp = s.post(
    url,
    data=data,
    headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": url,
        "Origin": "https://www.mcxindia.com",
    },
)

# 4) Save response as CSV
with open("mcx_bhavcopy.csv", "wb") as f:
    f.write(resp.content)

print(resp.status_code, resp.headers.get("content-type"))