import os
import re
import json
import shutil
import asyncio
import argparse
import platform
import gc
import posixpath
from urllib.parse import urlsplit, urlunsplit
import importlib
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
import html2text
import aiofiles

chardet = importlib.import_module("chardet") if importlib.util.find_spec("chardet") else None

tags_to_remove = ["iframe", "object", "script", "br", "img", "meta", "link", "input"]
classes_to_remove = [
    "collapsibleAreaRegion",
    "collapsibleRegionTitle",
    "collapseToggle",
    "codeSnippetContainerTab",
    "codeSnippetToolBar",
    "codeSnippetContainerTabs",
    "pageHeader",
    "feedbackLink",
    "userDataStyle",
]
ids_to_remove = [
    "PageFooter",
    "PageHeader",
    "TopicContent",
    "userDataCache",
    "HT_MailLink",
]

ENCODING_CANDIDATES = ["utf-8", "gb18030", "gbk", "gb2312"]
DTDUCAS_LOGO = """
 .----------------.  .----------------.  .----------------.  .----------------.  .----------------.  .----------------.  .----------------. 
| .--------------. || .--------------. || .--------------. || .--------------. || .--------------. || .--------------. || .--------------. |
| |  ________    | || |  _________   | || |  ________    | || | _____  _____ | || |     ______   | || |      __      | || |    _______   | |
| | |_   ___ `.  | || | |  _   _  |  | || | |_   ___ `.  | || ||_   _||_   _|| || |   .' ___  |  | || |     /  \     | || |   /  ___  |  | |
| |   | |   `. \ | || | |_/ | | \_|  | || |   | |   `. \ | || |  | |    | |  | || |  / .'   \_|  | || |    / /\ \    | || |  |  (__ \_|  | |
| |   | |    | | | || |     | |      | || |   | |    | | | || |  | '    ' |  | || |  | |         | || |   / ____ \   | || |   '.___`-.   | |
| |  _| |___.' / | || |    _| |_     | || |  _| |___.' / | || |   \ `--' /   | || |  \ `.___.'\  | || | _/ /    \ \_ | || |  |`\____) |  | |
| | |________.'  | || |   |_____|    | || | |________.'  | || |    `.__.'    | || |   `._____.'  | || ||____|  |____|| || |  |_______.'  | |
| |              | || |              | || |              | || |              | || |              | || |              | || |              | |
| '--------------' || '--------------' || '--------------' || '--------------' || '--------------' || '--------------' || '--------------' |
 '----------------'  '----------------'  '----------------'  '----------------'  '----------------'  '----------------'  '----------------' 
Duong Tran Quang - DTDucas (baymax.contact@gmail.com)
"""


def detect_file_encoding(file_path):
    """Detect file encoding using chardet, with fallback to common Chinese encodings."""
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read(10000)  # Read first 10KB for detection

        # BOM-first fast paths
        if raw_data.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"

        if chardet:
            result = chardet.detect(raw_data)
            encoding = result.get('encoding', 'utf-8') or 'utf-8'
            confidence = result.get('confidence', 0)
        else:
            encoding = 'utf-8'
            confidence = 0

        if encoding.lower() in ENCODING_CANDIDATES and confidence >= 0.7:
            return encoding

        # For low confidence or uncertain result, try preferred encodings
        if confidence < 0.7:
            for enc in ENCODING_CANDIDATES:
                try:
                    with open(file_path, 'r', encoding=enc) as f:
                        f.read(1000)
                    return enc
                except (UnicodeDecodeError, LookupError, OSError):
                    continue
        return encoding
    except OSError:
        # Fallback to trying common encodings
        for enc in ENCODING_CANDIDATES:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    f.read(1000)
                return enc
            except (UnicodeDecodeError, LookupError, OSError):
                continue
        return 'utf-8'


async def read_file_with_encoding(file_path):
    """Read file with automatic encoding detection."""
    loop = asyncio.get_running_loop()
    # Detect encoding in thread pool to avoid blocking
    encoding = await loop.run_in_executor(None, detect_file_encoding, file_path)
    # Read with detected encoding
    async with aiofiles.open(file_path, 'r', encoding=encoding, errors='replace') as f:
        return await f.read()


def extract_page_title(soup):
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        title = title_tag.string.strip()
        return title
    h1_tag = soup.find("h1")
    if h1_tag and h1_tag.get_text():
        title = h1_tag.get_text().strip()
        return title
    for heading_level in range(2, 7):
        heading = soup.find(f"h{heading_level}")
        if heading and heading.get_text():
            title = heading.get_text().strip()
            return title
    return None


def extract_keywords(title):
    if not title:
        return []
    stopwords = {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "with",
        "on",
        "at",
        "by",
        "is",
        "are",
        "was",
        "were",
    }
    words = re.findall(r"\b\w+\b", title.lower())
    keywords = [word for word in words if word not in stopwords and len(word) > 2]
    if title.lower() not in keywords:
        keywords.append(title.lower())
    return keywords


def normalize_doc_key(path_value):
    normalized = (path_value or "").replace("\\", "/").strip()
    normalized = normalized.lstrip("./")
    normalized = posixpath.normpath(normalized)
    if normalized == ".":
        return ""
    return normalized.lower()


def list_html_files_recursive(root_folder):
    html_files = []
    for current_root, _, filenames in os.walk(root_folder):
        for filename in filenames:
            if filename.lower().endswith((".htm", ".html")):
                full_path = os.path.join(current_root, filename)
                rel_path = os.path.relpath(full_path, root_folder)
                html_files.append(rel_path)
    return sorted(html_files)


def normalize_href_to_markdown(href):
    if not href:
        return href
    parsed = urlsplit(href)
    path = parsed.path or ""
    if path.lower().endswith((".htm", ".html")):
        base, _ = os.path.splitext(path)
        path = f"{base}.md"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def update_links(soup, file_dictionary=None, current_doc_key=None):
    link_title_map = {}
    current_dir_key = normalize_doc_key(posixpath.dirname(current_doc_key or ""))

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href == "#PageHeader":
            a.decompose()
        elif href.lower().startswith(("javascript:", "mailto:")):
            a.decompose()
        else:
            markdown_href = normalize_href_to_markdown(href)
            a["href"] = markdown_href
            existing_title = a.get("title", "").strip()
            link_title = existing_title
            parsed = urlsplit(href)
            if file_dictionary and parsed.path and not parsed.scheme:
                target_path = parsed.path.replace("\\", "/")
                if target_path.lower().endswith((".htm", ".html")):
                    resolved_path = normalize_doc_key(
                        posixpath.normpath(posixpath.join(current_dir_key, target_path))
                    )
                    target_key = os.path.splitext(resolved_path)[0]
                    file_info = file_dictionary.get(target_key)
                    if not file_info:
                        base_key = os.path.splitext(os.path.basename(target_key))[0]
                        matched_keys = [
                            key for key in file_dictionary.keys() if key.endswith(f"/{base_key}")
                        ]
                        if base_key in file_dictionary:
                            matched_keys.append(base_key)
                        if len(matched_keys) == 1:
                            file_info = file_dictionary.get(matched_keys[0])
                    if file_info:
                        display_name = file_info.get("title")
                        if display_name:
                            a["title"] = display_name
                            link_title = display_name
            if link_title:
                link_title_map[markdown_href] = link_title
    return soup, link_title_map


def apply_link_titles(markdown_text, link_title_map):
    if not link_title_map:
        return markdown_text

    pattern = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

    def repl(match):
        text, url = match.group(1), match.group(2)
        title = link_title_map.get(url)
        if not title:
            return match.group(0)
        safe_title = title.replace('"', r"\"")
        return f'[{text}]({url} "{safe_title}")'

    return pattern.sub(repl, markdown_text)


def remove_unwanted_elements(soup):
    semantic_tags = ["style", "nav", "header", "footer"]
    for tag in semantic_tags:
        for element in soup.find_all(tag):
            element.decompose()
    for tag in tags_to_remove:
        for element in soup.find_all(tag):
            element.decompose()
    for tag in soup.find_all(
        lambda tag: tag.has_attr("class")
        and any(cls in tag.get("class") for cls in classes_to_remove)
    ):
        tag.decompose()
    for element_id in ids_to_remove:
        for tag in soup.find_all(id=element_id):
            tag.decompose()
    for a in soup.find_all(
        "a", href=lambda href: href and "javascript:" in str(href).lower()
    ):
        a.decompose()
    for a in soup.find_all(
        "a", href=lambda href: href and "mailto:" in str(href).lower()
    ):
        a.decompose()
    for element in soup.find_all(attrs={"role": lambda r: r in {"navigation", "banner", "contentinfo"}}):
        element.decompose()
    return soup


def replace_code_snippets(soup):
    id_to_lang = {
        "IDAB_code_Div1": "csharp",
        "IDAB_code_Div2": "vb",
        "IDAB_code_Div3": "cpp",
        "IDAB_code_Div4": "fsharp",
    }
    code_blocks = {}
    counter = 0
    for div_id, lang in id_to_lang.items():
        for code_div in soup.find_all("div", id=div_id):
            counter += 1
            pre_tag = code_div.find("pre")
            if pre_tag:
                code_text = pre_tag.get_text()
            else:
                code_text = code_div.get_text()
            placeholder = f"<<CODE_BLOCK_{counter}>>"
            code_block_markdown = f"```{lang}\n{code_text}\n```\n"
            code_blocks[placeholder] = code_block_markdown
            new_node = soup.new_string(placeholder)
            code_div.replace_with(new_node)
    for pre_tag in soup.find_all("pre"):
        counter += 1
        code_text = pre_tag.get_text()
        lang = "text"
        class_attr = pre_tag.get("class", [])
        if class_attr:
            class_str = " ".join(class_attr)
            if "csharp" in class_str or "cs" in class_str:
                lang = "csharp"
            elif "vb" in class_str:
                lang = "vb"
            elif "cpp" in class_str or "c++" in class_str:
                lang = "cpp"
            elif "fsharp" in class_str or "fs" in class_str:
                lang = "fsharp"
            elif "xml" in class_str or "html" in class_str:
                lang = "xml"
            elif "json" in class_str:
                lang = "json"
        data_lang = (pre_tag.get("data-language") or pre_tag.get("lang") or "").lower()
        if data_lang in {"c#", "csharp"}:
            lang = "csharp"
        elif data_lang in {"vb", "vb.net", "vbnet"}:
            lang = "vb"
        elif data_lang in {"cpp", "c++"}:
            lang = "cpp"
        elif data_lang in {"f#", "fsharp"}:
            lang = "fsharp"
        elif data_lang in {"xml", "html"}:
            lang = "xml"
        elif data_lang == "json":
            lang = "json"
        placeholder = f"<<CODE_BLOCK_{counter}>>"
        code_block_markdown = f"```{lang}\n{code_text}\n```\n"
        code_blocks[placeholder] = code_block_markdown
        new_node = soup.new_string(placeholder)
        pre_tag.replace_with(new_node)
    return soup, code_blocks


def clean_markdown_formatting(markdown_text):
    markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text)
    lines = markdown_text.splitlines()
    for i in range(len(lines)):
        for level in range(1, 7):
            header_pattern = r"^#{" + str(level) + r"}([^\s])"
            if re.match(header_pattern, lines[i]):
                lines[i] = re.sub(header_pattern, "#" * level + r" \1", lines[i])
    formatted_text = "\n".join(lines)
    formatted_text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f"[{m.group(1)}]({m.group(2)})",
        formatted_text,
    )
    guid_pattern = (
        r"\(([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.md\)"
    )
    formatted_text = re.sub(guid_pattern, r"(reference-\1.md)", formatted_text)
    formatted_text = re.sub(r"\[.*?\]\(javascript:.*?\)", "", formatted_text)
    formatted_text = re.sub(r"See Also \[Send Feedback\].*?---", "---", formatted_text)
    formatted_text = re.sub(r"Collapse AllExpand All", "", formatted_text)
    formatted_text = re.sub(r"Code: All Code: Multiple.*?---", "---", formatted_text)
    formatted_text = re.sub(r"\[Send comments.*?\].*?\)", "", formatted_text)
    formatted_text = re.sub(r"---\s*---", "---", formatted_text)
    return formatted_text


def fix_tables(markdown_text):
    lines = markdown_text.splitlines()
    fixed_lines = []
    i = 0
    while i < len(lines):
        if (
            "|" in lines[i]
            and i + 1 < len(lines)
            and re.match(r"^[\s\-\|:]+$", lines[i + 1])
        ):
            table_lines = []
            while i < len(lines) and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            fixed_table_lines = fix_table_block(table_lines)
            fixed_lines.extend(fixed_table_lines)
            fixed_lines.append("")
        else:
            fixed_lines.append(lines[i])
            i += 1
    return "\n".join(fixed_lines)


def fix_table_block(table_lines):
    split_lines = [[cell.strip() for cell in line.split("|")] for line in table_lines]
    processed_lines = []
    for row in split_lines:
        if row and row[0] == "":
            row = row[1:]
        if row and row[-1] == "":
            row = row[:-1]
        processed_lines.append(row)
    formatted_lines = []
    for i, row in enumerate(processed_lines):
        line = "| " + " | ".join(row) + " |"
        formatted_lines.append(line)
        if i == 0:
            separator = "| " + " | ".join(["---" for _ in row]) + " |"
            formatted_lines.append(separator)
    return formatted_lines


def convert_html_to_markdown(
    html_content, file_dictionary=None, version=None, source_doc_key=None
):
    soup = BeautifulSoup(html_content, "html.parser")
    main_title = extract_page_title(soup)
    soup = remove_unwanted_elements(soup)
    soup, link_title_map = update_links(soup, file_dictionary, source_doc_key)
    soup, code_blocks = replace_code_snippets(soup)
    modified_html = str(soup)
    h = html2text.HTML2Text()
    h.body_width = 0
    h.ignore_links = False
    h.ignore_images = False
    h.ignore_tables = False
    h.single_line_break = True
    h.unicode_snob = True
    markdown_text = h.handle(modified_html)
    if main_title:
        if version:
            markdown_text = f"# {main_title} ({version})\n\n{markdown_text}"
        else:
            markdown_text = f"# {main_title}\n\n{markdown_text}"
    for placeholder, code_block in code_blocks.items():
        markdown_text = markdown_text.replace(placeholder, code_block)
    markdown_text = apply_link_titles(markdown_text, link_title_map)
    markdown_text = fix_tables(markdown_text)
    markdown_text = clean_markdown_formatting(markdown_text)
    return markdown_text


def clear_folder(folder_path):
    if os.path.exists(folder_path):
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                print(f"Failed to delete {file_path}. Reason: {e}")
    else:
        os.makedirs(folder_path)


async def export_chm_to_htm(chm_path, export_folder):
    if not os.path.exists(export_folder):
        os.makedirs(export_folder)
    clear_folder(export_folder)

    # Detect platform and set appropriate 7z command
    if platform.system() == "Windows":
        seven_zip = r"C:\Program Files\7-Zip\7z.exe"
        if not os.path.exists(seven_zip):
            print(
                "7z.exe not found. Please install 7-Zip and update the seven_zip path accordingly."
            )
            return False
    else:
        # Linux/macOS: use '7z' or '7zz' from PATH
        seven_zip = "7z"
        # Check if 7z is available
        if not shutil.which(seven_zip):
            seven_zip = "7zz"
            if not shutil.which(seven_zip):
                print(
                    "7z not found. Please install p7zip-full: sudo apt install p7zip-full"
                )
                return False
    def has_extracted_html(target_folder):
        html_folder = find_html_folder(target_folder)
        if not html_folder:
            return False
        return len(list_html_files_recursive(html_folder)) > 0

    def extract_failed_entries(text):
        failed = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if "ERROR:" in line and "Data Error" in line:
                # Typical format:
                # ERROR: Data Error : contents/assets/images/xxx.png
                parts = line.split(":", 2)
                if len(parts) == 3:
                    failed_path = parts[2].strip()
                    if failed_path:
                        failed.append(failed_path)
                else:
                    failed.append(line)
        return failed

    def write_unextract_log(target_folder, failed_files):
        if not failed_files:
            return
        log_path = os.path.join(target_folder, "unextractfile.log")
        unique_failed = sorted(set(failed_files))
        version_name = os.path.basename(os.path.normpath(target_folder))
        suggested_root = os.path.join("output", version_name, "data")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("# Files failed to extract from CHM (manual follow-up)\n")
            f.write("# Format: <failed_path> => <suggested_manual_target>\n")
            for item in unique_failed:
                normalized_item = item.replace("\\", "/").lstrip("/")
                suggested_target = os.path.join(suggested_root, normalized_item).replace(
                    "\\", "/"
                )
                f.write(f"{normalized_item} => {suggested_target}\n")
        print(f"Saved failed extraction list to: {log_path}")

    async def run_7z_extract(extra_args=None):
        cmd = [seven_zip, "x", chm_path, f"-o{export_folder}", "-y"]
        if extra_args:
            cmd.extend(extra_args)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return process.returncode, stdout.decode(errors="ignore"), stderr.decode(
            errors="ignore"
        )

    try:
        failed_entries = []
        # Step 1: Prefer extracting only HTML files to avoid non-critical
        # image/document CRC errors that commonly appear in some CHM packages.
        rc, stdout, stderr = await run_7z_extract(["-ir!*.htm", "-ir!*.html"])
        failed_entries.extend(extract_failed_entries(stdout))
        failed_entries.extend(extract_failed_entries(stderr))
        if rc == 0 and has_extracted_html(export_folder):
            write_unextract_log(export_folder, failed_entries)
            return True
        if has_extracted_html(export_folder):
            print(
                f"Warning: 7z returned code {rc} for {chm_path}, but HTML files were extracted. Continuing."
            )
            if stderr.strip():
                print(stderr)
            write_unextract_log(export_folder, failed_entries)
            return True

        # Step 2: Fallback to full extraction if HTML-only extraction did not work
        rc, stdout, stderr = await run_7z_extract()
        failed_entries.extend(extract_failed_entries(stdout))
        failed_entries.extend(extract_failed_entries(stderr))
        if rc == 0 and has_extracted_html(export_folder):
            write_unextract_log(export_folder, failed_entries)
            return True
        if has_extracted_html(export_folder):
            print(
                f"Warning: full extraction returned code {rc} for {chm_path}, but HTML files were extracted. Continuing."
            )
            if stderr.strip():
                print(stderr)
            write_unextract_log(export_folder, failed_entries)
            return True

        print(f"Error extracting {chm_path}:")
        if stderr.strip():
            print(stderr)
        elif stdout.strip():
            print(stdout)
        write_unextract_log(export_folder, failed_entries)
        return False
    except Exception as e:
        print(f"Error extracting CHM file using 7z.exe: {e}")
        return False


def find_html_folder(input_folder):
    """Find the folder containing HTML files. CHM files can have different structures."""
    html_folder = os.path.join(input_folder, "html")
    if os.path.exists(html_folder):
        return html_folder

    # Check if HTML files are directly in the input folder
    html_files = [
        f for f in os.listdir(input_folder)
        if f.lower().endswith((".htm", ".html"))
    ]
    if html_files:
        return input_folder

    return None


async def build_file_dictionary(input_folder, version=None):
    html_folder = find_html_folder(input_folder)
    if not html_folder:
        print(f"No HTML folder or files found in: {input_folder}")
        return {}
    file_dictionary = {}
    semaphore = asyncio.Semaphore(20)

    async def process_file_for_dict(relative_path):
        input_path = os.path.join(html_folder, relative_path)
        key = os.path.splitext(normalize_doc_key(relative_path))[0]
        output_filename = os.path.splitext(relative_path.replace("\\", "/"))[0] + ".md"
        try:
            async with semaphore:
                html_content = await read_file_with_encoding(input_path)
            soup = BeautifulSoup(html_content, "html.parser")
            title = extract_page_title(soup)
            file_info = {
                "title": title if title else "Untitled Document",
                "filename": output_filename,
            }
            if version:
                file_info["version"] = version
            return key, file_info
        except Exception as e:
            print(f"Error processing {input_path} for dictionary: {e}")
            return key, {
                "title": "Error Document",
                "filename": output_filename,
                "version": version if version else None,
            }

    file_list = list_html_files_recursive(html_folder)
    print(f"Building dictionary from {len(file_list)} HTML files...")
    batch_size = 100
    for i in range(0, len(file_list), batch_size):
        batch_files = file_list[i : i + batch_size]
        batch_tasks = [process_file_for_dict(relative_path) for relative_path in batch_files]
        results = await asyncio.gather(*batch_tasks)
        for key, info in results:
            file_dictionary[key] = info
        if (i // batch_size + 1) % 10 == 0:
            gc.collect()
    print(f"Dictionary built with {len(file_dictionary)} entries")
    return file_dictionary


async def convert_files_with_dictionary(
    input_folder,
    output_folder,
    data_folder,
    core_folder,
    file_dictionary,
    version=None,
    max_workers=4,
    semaphore_limit=20,
    batch_size=10,
):
    html_folder = find_html_folder(input_folder)
    if not html_folder:
        print(f"No HTML folder or files found in: {input_folder}")
        return
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    if not os.path.exists(data_folder):
        os.makedirs(data_folder)
    if not os.path.exists(core_folder):
        os.makedirs(core_folder)
    file_list = list_html_files_recursive(html_folder)
    total_files = len(file_list)
    print(f"Converting {total_files} HTML files to Markdown...")
    semaphore = asyncio.Semaphore(semaphore_limit)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for i in range(0, total_files, batch_size):
            batch_files = file_list[i : i + batch_size]
            batch_tasks = []
            for relative_path in batch_files:
                input_path = os.path.join(html_folder, relative_path)
                output_rel_path = (
                    os.path.splitext(relative_path.replace("\\", "/"))[0] + ".md"
                )
                output_path = os.path.join(data_folder, output_rel_path)
                batch_tasks.append(
                    process_file(
                        executor,
                        input_path,
                        output_path,
                        semaphore,
                        file_dictionary,
                        version,
                        os.path.splitext(normalize_doc_key(relative_path))[0],
                    )
                )
            await asyncio.gather(*batch_tasks)
            batch_number = (i // batch_size) + 1
            processed = min(i + batch_size, total_files)
            remaining = total_files - processed
            print(
                f"Batch {batch_number}: Processed {len(batch_files)} files. {remaining} remaining."
            )
            if batch_number % 50 == 0:
                gc.collect()
    await create_index_files(core_folder, file_dictionary, version)


async def process_file(
    executor,
    input_path,
    output_path,
    semaphore,
    file_dictionary,
    version=None,
    source_doc_key=None,
):
    loop = asyncio.get_running_loop()
    try:
        async with semaphore:
            html_content = await read_file_with_encoding(input_path)
        markdown_content = await loop.run_in_executor(
            executor,
            convert_html_to_markdown,
            html_content,
            file_dictionary,
            version,
            source_doc_key,
        )
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        async with semaphore:
            async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
                await f.write(markdown_content)
    except Exception as e:
        print(f"Error processing {input_path}: {e}")


async def create_index_files(core_folder, file_dictionary, version=None):
    dictionary_path = os.path.join(core_folder, "file_index.json")
    async with aiofiles.open(dictionary_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(file_dictionary, indent=4, ensure_ascii=False))
    print(f"Created file index dictionary at {dictionary_path}")
    id_lookup = {}
    for file_id, info in file_dictionary.items():
        clean_id = file_id.lower()
        id_lookup[clean_id] = {
            "title": info["title"],
            "filename": info["filename"],
            "keywords": extract_keywords(info["title"]),
        }
        if version:
            id_lookup[clean_id]["version"] = version
        elif "version" in info:
            id_lookup[clean_id]["version"] = info["version"]
    id_lookup_path = os.path.join(core_folder, "id_lookup.json")
    async with aiofiles.open(id_lookup_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(id_lookup, indent=4, ensure_ascii=False))
    print(f"Created ID-based lookup dictionary at {id_lookup_path}")
    md_index_path = os.path.join(core_folder, "index.md")
    async with aiofiles.open(md_index_path, "w", encoding="utf-8") as f:
        if version:
            await f.write(f"# API Documentation Index - Version {version}\n\n")
        else:
            await f.write("# API Documentation Index\n\n")
        sorted_entries = sorted(
            file_dictionary.items(), key=lambda x: x[1]["title"].lower()
        )
        current_letter = None
        all_first_letters = sorted(
            set(
                entry[1]["title"][0].upper()
                for entry in sorted_entries
                if entry[1]["title"]
            )
        )
        alpha_links = [f"[{letter}](#{letter.lower()})" for letter in all_first_letters]
        await f.write("## Quick Navigation\n\n")
        await f.write(" | ".join(alpha_links) + "\n\n")
        for file_id, info in sorted_entries:
            title = info["title"]
            if not title:
                continue
            first_letter = title[0].upper()
            if first_letter != current_letter:
                current_letter = first_letter
                await f.write(
                    f"\n## {current_letter}\n<a id='{current_letter.lower()}'></a>\n\n"
                )
            filename = info["filename"]
            await f.write(f"- [{title}](../data/{filename})\n")
    print(f"Created Markdown index at {md_index_path}")


async def process_chm_file(
    chm_file_path,
    base_input_folder,
    base_output_folder,
    max_workers=4,
    semaphore_limit=20,
    batch_size=10,
):
    version = os.path.splitext(os.path.basename(chm_file_path))[0]
    print(f"\n=== Processing {version} ===")
    input_folder = os.path.join(base_input_folder, version)
    output_folder = os.path.join(base_output_folder, version)
    data_folder = os.path.join(output_folder, "data")
    core_folder = os.path.join(output_folder, "core")
    for folder in [input_folder, output_folder, data_folder, core_folder]:
        if not os.path.exists(folder):
            os.makedirs(folder)
    print(f"Extracting {chm_file_path} to {input_folder}...")
    success = await export_chm_to_htm(chm_file_path, input_folder)
    if not success:
        print(f"Failed to extract {chm_file_path}. Skipping this version.")
        return False
    file_dictionary = await build_file_dictionary(input_folder, version)
    await convert_files_with_dictionary(
        input_folder,
        output_folder,
        data_folder,
        core_folder,
        file_dictionary,
        version,
        max_workers,
        semaphore_limit,
        batch_size,
    )
    shutil.rmtree(os.path.join(input_folder, "html"), ignore_errors=True)
    print(f"\n=== Completed processing {version} ===")
    return True


async def process_all_chm_files(
    resources_folder,
    base_input_folder,
    base_output_folder,
    max_workers=8,
    semaphore_limit=20,
    batch_size=50,
):
    chm_files = [
        os.path.join(resources_folder, f)
        for f in os.listdir(resources_folder)
        if f.lower().endswith(".chm")
    ]
    if not chm_files:
        print(f"No CHM files found in {resources_folder}.")
        return
    print(f"Found {len(chm_files)} CHM files to process.")
    results = []
    for chm_file in chm_files:
        result = await process_chm_file(
            chm_file,
            base_input_folder,
            base_output_folder,
            max_workers,
            semaphore_limit,
            batch_size,
        )
        results.append((chm_file, result))
    print("\n=== Processing Summary ===")
    success_count = sum(1 for _, result in results if result)
    print(f"Successfully processed {success_count} out of {len(chm_files)} CHM files.")
    if success_count < len(chm_files):
        print("Failed to process the following CHM files:")
        for chm_file, result in results:
            if not result:
                print(f"  - {os.path.basename(chm_file)}")


async def main():
    print(DTDUCAS_LOGO)
    parser = argparse.ArgumentParser(description="Convert CHM files to Markdown format")
    parser.add_argument("--single", "-s", help="Process a single CHM file")
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Process all CHM files in resources folder",
    )
    parser.add_argument(
        "--keep-html",
        "-k",
        action="store_true",
        help="Keep HTML files after conversion",
    )
    parser.add_argument(
        "--workers", "-w", type=int, default=8, help="Number of worker threads"
    )
    parser.add_argument(
        "--batch-size", "-b", type=int, default=50, help="Batch size for processing"
    )
    parser.add_argument(
        "--semaphore",
        type=int,
        default=20,
        help="Semaphore limit for concurrent operations",
    )
    args = parser.parse_args()
    resources_folder = r"resources"
    base_input_folder = r"extracted"
    base_output_folder = r"output"
    if not os.path.exists(base_output_folder):
        os.makedirs(base_output_folder)
    if args.single:
        chm_file_path = args.single
        if not os.path.exists(chm_file_path):
            chm_file_path = os.path.join(resources_folder, chm_file_path)
            if not os.path.exists(chm_file_path):
                print(f"CHM file not found: {args.single}")
                return
        await process_chm_file(
            chm_file_path,
            base_input_folder,
            base_output_folder,
            args.workers,
            args.semaphore,
            args.batch_size,
        )
    elif args.all:
        await process_all_chm_files(
            resources_folder,
            base_input_folder,
            base_output_folder,
            args.workers,
            args.semaphore,
            args.batch_size,
        )
    else:
        chm_files = [
            f for f in os.listdir(resources_folder) if f.lower().endswith(".chm")
        ]
        if not chm_files:
            print(
                f"No CHM files found in {resources_folder}. Please add CHM files to this folder."
            )
            return
        print("Available CHM files:")
        for i, chm_file in enumerate(chm_files, 1):
            print(f"{i}. {chm_file}")
        try:
            choice = input(
                "\nEnter the number of the CHM file to process (or 'a' for all): "
            )
            if choice.lower() in ["a", "all"]:
                await process_all_chm_files(
                    resources_folder,
                    base_input_folder,
                    base_output_folder,
                    args.workers,
                    args.semaphore,
                    args.batch_size,
                )
            else:
                try:
                    index = int(choice) - 1
                    if 0 <= index < len(chm_files):
                        await process_chm_file(
                            os.path.join(resources_folder, chm_files[index]),
                            base_input_folder,
                            base_output_folder,
                            args.workers,
                            args.semaphore,
                            args.batch_size,
                        )
                    else:
                        print("Invalid selection.")
                except ValueError:
                    print("Invalid input. Please enter a number or 'a'.")
        except Exception as e:
            print(f"Error during processing: {e}")
    if not args.keep_html and os.path.exists(base_input_folder):
        shutil.rmtree(base_input_folder)
    print("Conversion completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
