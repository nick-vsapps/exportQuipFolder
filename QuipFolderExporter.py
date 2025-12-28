import time
from playwright.sync_api import sync_playwright
import requests
from pathlib import Path
import re
import json
from config import *

# Variables from config.py are in ALL CAPS, except for the following config derived variables:
OUTPUT_PATH = Path(OUTPUT_FOLDER)  # Relative path or absolute path
if TESTING:
    MANIFEST_PATH = Path("test-export") / MANIFEST_FILE
else:
    MANIFEST_PATH = Path(OUTPUT_PATH) / MANIFEST_FILE

def _sanitize(name):
    """Remove characters that break filenames."""
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()


def _getFolderThreads(folder_id: str) -> list[str]:
    """
    Returns a list of thread IDs for a given folder ID
    """
    endpoint = f"{API_BASE}/1/folders/{folder_id}"
    response = requests.get(endpoint, headers={"Authorization": f"Bearer {API_TOKEN}"})
    response.raise_for_status()
    data = response.json()
    children = data.get("children", [])
    title = data["folder"]["title"]
    print(f"{len(children)} threads found in folder {title}")

    threads = []

    for child in children:
        if "thread_id" in child:
            threads.append(child["thread_id"])
        elif "folder_id" in child:
            # recurse into subfolder
            threads.extend(_getFolderThreads(child["folder_id"]))

    return threads


def traverseFolder(manifest, page: object, folder_id: str, currentDir: Path):
    """
    Recursively traverse a Quip folder and export all documents as Markdown
    """
    url = f"{API_BASE}/1/folders/{folder_id}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {API_TOKEN}"})
    resp.raise_for_status()
    data = resp.json()

    # Store folder metadata
    folder_meta = data.get("folder", {})

    # Create directory for this folder
    new_folder_path = currentDir / _sanitize(folder_meta.get("title"))
    new_folder_path.mkdir(parents=True, exist_ok=True)
  
    children_list = data.get("children", [])
    print(f"Traversing folder: {folder_meta.get('title')} with {len(children_list)} children")

    for child in children_list:
        if "thread_id" in child: #Check if child is a document, 
            #if child["thread_id"] not in [entry["docID"] for entry in manifest]:
            exportDocumentAsMarkdown(page, child['thread_id'], new_folder_path)            
        elif "folder_id" in child:
            traverseFolder(manifest, page, child["folder_id"], new_folder_path)
    return

def exportDocumentAsMarkdown(page: object, docID, outputPath):
    """
    Save a Quip document locally as Markdown using Playwright
    """
    
    # Get document metadata
    api_url = f"{API_BASE}/2/threads/{docID}"
    api_resp = requests.get(api_url, headers={"Authorization": f"Bearer {API_TOKEN}"})
    api_resp.raise_for_status()
    ratelimit_remaining = api_resp.headers.get("X-RateLimit-Remaining")
    retry_after = api_resp.headers.get("Retry-After")
    api_data = api_resp.json()
    doc_title = api_data.get("thread", {}).get("title")

    filePath = outputPath / f"{_sanitize(doc_title)}.md"
    
    if int(ratelimit_remaining) < 5:
        wait_time = int(retry_after) + 1
        print(f"Approaching API rate limit, waiting for {wait_time} seconds...")
        time.sleep(wait_time)
    
    if DUPE_CHECK and filePath.exists():
        print(f"Document {filePath.absolute()} already exists, skipping export...")
        return

    # Export markdown via Playwright
    QUIP_DOC_URL = f"{QUIP_DOMAIN}/{docID}"
    page.goto(QUIP_DOC_URL)
    try:
        page.click("button:has-text('Document')")
    except:
        print("No document button found. Trying spreadsheet button...")
        try:
            page.click("button:has-text('Spreadsheet')")
        except:
            print(f"Could not find Document or Spreadsheet button for docID {docID}, skipping...")
            return
    page.hover("div.parts-menu-label:has-text('Export')")
    page.locator("div.parts-menu-label:has-text('Markdown')").click(force=True)

    # Wait for clipboard to be populated
    #while page.evaluate("navigator.clipboard.readText()") is None or page.evaluate("navigator.clipboard.readText()") == "":
        #time.sleep(0.05)
        
    markdown_content = page.evaluate("navigator.clipboard.readText()")

    # Insert Creation and Modification Dates
    creation_usec = api_data.get("thread", {}).get("created_usec")
    modified_usec = api_data.get("thread", {}).get("updated_usec")
    creation_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(creation_usec / 1_000_000))
    modified_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(modified_usec / 1_000_000))

    markdown_content = re.sub(r'\r', '', markdown_content)  # Normalize line endings
    lines = markdown_content.split("\n", maxsplit=1)
    lines.insert(1, f"\nDocID: {docID}\nCreation Date: {creation_time}\nModification Date: {modified_time}\n\n")
    markdown_content = "".join(lines)

    # Save to file
    filePath = outputPath / f"{_sanitize(doc_title)}.md"
    with open(filePath, 'w', encoding='utf-8') as f:
        f.write(markdown_content)

        # Record metadata
        # f.write(f"\n\n<!-- Exported Document Metadata -->\n")

    print(f"Exported document {filePath.absolute()} successfully. Updating manifest...")

    # Save manifest
    manifest_entry = {"docID": docID, "title": doc_title, "filePath": str(filePath.absolute())}
    
    with open(MANIFEST_PATH, 'a', encoding='utf-8') as f:
        f.write(",\n")
        json.dump(manifest_entry, f, indent=4)

def main():
        # API Endpoints
    currentUserEndpoint = f"1/users/current"
    getThreadEndpoint = f"2/threads"

    print("Getting user info from Quip API...")
    response = requests.get(f"{API_BASE}/{currentUserEndpoint}", headers={"Authorization": f"Bearer {API_TOKEN}"})
    response.raise_for_status()        
    
    privateFolderId = response.json()["private_folder_id"]
    specificFolderId = "qQb5O0BwTKet"  # can be changed to any folder ID
    print("User info saved.")

    manifest = []

    with sync_playwright() as p:
        if SLOW_MO:
            browser = p.chromium.launch(headless = False, slow_mo=1500)
        else:
            browser = p.chromium.launch(headless = False)
        
        page = browser.new_page()
        page.goto(QUIP_DOMAIN)
             
        print("Logging in to Google...")
        page.fill('input[name="email"]', USER_EMAIL)
        page.click("button:has-text('Continue')")
        time.sleep(1)
        page.click("button:has-text('Next')")
        page.locator("input[type='password']").fill(USER_PASSWORD)
        page.click("button:has-text('Next')")

        # Wait for User Authentication
        input("Press enter after logging in")

        # Traverse folder tree
        if TESTING:
            traverseFolder(manifest, page, EXAMPLE_FOLDER_ID, Path("test-export"))
        else:
            traverseFolder(manifest, page, specificFolderId, OUTPUT_PATH)
            
        browser.close()

if __name__ == "__main__":
    main()