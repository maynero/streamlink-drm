import json
import re
from typing import Any
from urllib.parse import parse_qsl

from lxml.etree import HTML, XML

from streamlink.compat import detect_encoding
from streamlink.exceptions import PluginError


def _parse(parser, data, name, exception, schema, *args, **kwargs):
    try:
        parsed = parser(data, *args, **kwargs)
    except Exception as err:
        snippet = repr(data)
        if len(snippet) > 35:
            snippet = f"{snippet[:35]} ..."

        raise exception(f"Unable to parse {name}: {err} ({snippet})")  # noqa: B904

    if schema:
        parsed = schema.validate(parsed, name=name, exception=exception)

    return parsed


def parse_json(
    data,
    name="JSON",
    exception=PluginError,
    schema=None,
    *args,
    **kwargs,
):
    """Wrapper around json.loads.

    Provides these extra features:
     - Wraps errors in custom exception with a snippet of the data in the message
    """
    return _parse(json.loads, data, name, exception, schema, *args, **kwargs)


def parse_html(
    data,
    name="HTML",
    exception=PluginError,
    schema=None,
    *args,
    **kwargs,
) -> Any:
    """Wrapper around lxml.etree.HTML with some extras.

    Provides these extra features:
     - Removes XML declarations of invalid XHTML5 documents
     - Wraps errors in custom exception with a snippet of the data in the message
    """
    # strip XML text declarations from XHTML5 documents which were incorrectly defined as HTML5
    is_bytes = isinstance(data, bytes)
    if data and data.lstrip()[:5].lower() == (b"<?xml" if is_bytes else "<?xml"):
        if is_bytes:
            # get the document's encoding using the "encoding" attribute value of the XML text declaration
            match = re.match(rb"^\s*<\?xml\s.*?encoding=(?P<q>[\'\"])(?P<encoding>.+?)(?P=q).*?\?>", data, re.IGNORECASE)
            if match and (encoding_value := detect_encoding(match["encoding"])["encoding"]):
                encoding: str | None = match["encoding"].decode(encoding_value)
            else:
                # no "encoding" attribute: try to figure out encoding from the document's content
                encoding = detect_encoding(data)["encoding"]

            if not encoding:
                raise exception(f"Unable to detect encoding of {name} payload")

            data = data.decode(encoding)

        data = re.sub(r"^\s*<\?xml.+?\?>", "", data)

    return _parse(HTML, data, name, exception, schema, *args, **kwargs)


def _clean_xml_content(data):
    """
    Clean XML content by removing extra content after the root closing tag.
    This handles malformed XML that has comments or scripts after the main content.
    """
    if isinstance(data, bytes):
        data_str = data.decode('utf-8', errors='ignore')
        is_bytes = True
    else:
        data_str = data
        is_bytes = False

    # Look for common root tag endings that might have extra content after them
    root_tags = ['</MPD>', '</playlist>', '</rss>', '</xml>', '</root>']

    for tag in root_tags:
        # Find the last occurrence of the closing tag (case insensitive)
        pattern = re.escape(tag).replace('\\>', r'\s*>')
        matches = list(re.finditer(pattern, data_str, re.IGNORECASE))

        if matches:
            # Get the last match and truncate everything after it
            last_match = matches[-1]
            cleaned_data = data_str[:last_match.end()]
            return cleaned_data.encode('utf-8') if is_bytes else cleaned_data

    # If no known root tags found, try to find any closing tag at the end and remove content after it
    # This is a more aggressive approach for unknown XML structures
    lines = data_str.split('\n')
    cleaned_lines = []
    found_closing_tag = False

    for line in lines:
        cleaned_lines.append(line)
        # Look for any closing tag that might be the root
        if re.search(r'<\/\w+\s*>$', line.strip()) and not found_closing_tag:
            # Check if this looks like it could be a root closing tag
            # (simple heuristic: it's not deeply indented)
            if len(line) - len(line.lstrip()) <= 4:  # 4 or fewer leading spaces/tabs
                found_closing_tag = True
                break

    if found_closing_tag and len(cleaned_lines) < len(lines):
        cleaned_data = '\n'.join(cleaned_lines)
        return cleaned_data.encode('utf-8') if is_bytes else cleaned_data

    # If no cleaning was possible, return original data
    return data


def parse_xml(
    data,
    ignore_ns=False,
    invalid_char_entities=False,
    name="XML",
    exception=PluginError,
    schema=None,
    *args,
    **kwargs,
):
    """Wrapper around lxml.etree.XML with some extras.

    Provides these extra features:
     - Handles incorrectly encoded XML
     - Allows stripping namespace information
     - Cleans malformed XML with extra content after root closing tags
     - Wraps errors in custom exception with a snippet of the data in the message
    """
    if isinstance(data, str):
        data = bytes(data, "utf8")
    if ignore_ns:
        data = re.sub(rb"\s+xmlns=\"(.+?)\"", b"", data)
    if invalid_char_entities:
        data = re.sub(rb"&(?!(?:#(?:[0-9]+|[Xx][0-9A-Fa-f]+)|[A-Za-z0-9]+);)", b"&amp;", data)

    # First attempt with original data
    try:
        return _parse(XML, data, name, exception, schema, *args, **kwargs)
    except exception as err:
        # If parsing failed and the error mentions "Extra content at the end of the document"
        if "Extra content at the end of the document" in str(err):
            try:
                # Try to clean the XML content
                cleaned_data = _clean_xml_content(data)
                return _parse(XML, cleaned_data, name, exception, schema, *args, **kwargs)
            except exception:
                # If cleaning didn't work, raise the original error
                raise err
        else:
            # For other parsing errors, raise immediately
            raise err


def parse_qsd(
    data,
    name="query string",
    exception=PluginError,
    schema=None,
    *args,
    **kwargs,
):
    """Parses a query string into a dict.

    Provides these extra features:
     - Unlike parse_qs and parse_qsl, duplicate keys are not preserved in favor of a simpler return value
     - Wraps errors in custom exception with a snippet of the data in the message
    """
    return _parse(lambda d: dict(parse_qsl(d, *args, **kwargs)), data, name, exception, schema)
