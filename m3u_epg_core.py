import re
import requests
from datetime import datetime
from lxml import etree
import io

def fetch_content(source_type, source_value):
    """
    Fetches content from an uploaded file or a URL.
    Returns (content_string, list_of_errors).
    """
    if source_type == 'file':
        try:
            # Assuming source_value is a file-like object (e.g., from open())
            return source_value.read().decode('utf-8', errors='ignore'), []
        except Exception as e:
            return None, [f"Error reading file: {e}"]
    elif source_type == 'url':
        try:
            response = requests.get(source_value, timeout=10)
            response.raise_for_status()
            return response.text, []
        except requests.exceptions.RequestException as e:
            return None, [f"Error fetching URL '{source_value}': {e}"]
        except Exception as e:
            return None, [f"An unexpected error occurred while fetching URL '{source_value}': {e}"]
    return None, ["Invalid source type provided."]

def sanitize_channel_name_for_id(name):
    """
    Sanitizes a channel name to be used as a tvg-id.
    Removes non-alphanumeric, replaces spaces/underscores with underscores, lowercase.
    """
    sane_name = re.sub(r'[^\w\s-]', '', name).strip()
    sane_name = re.sub(r'[\s-]+', '_', sane_name)
    sane_name = sane_name.lower()
    return sane_name

def format_attributes_for_extinf(attributes_dict):
    """Formats a dictionary of attributes into a string for EXTINF line."""
    formatted_attrs = []
    for key in sorted(attributes_dict.keys()):
        value = attributes_dict[key]
        if value is not None and str(value).strip() != "":
            formatted_attrs.append(f'{key}="{value}"')
    return " ".join(formatted_attrs)

def is_gracenote_id(tvg_id):
    """
    Checks if a tvg-id appears to be a Gracenote ID based on common patterns.
    Gracenote IDs often start with 'EP' or are numeric/alphanumeric followed by '.F.EP'
    or similar structures used by Channels DVR. This is a heuristic.
    """
    if not tvg_id:
        return False
    
    gracenote_pattern = re.compile(r"^(EP|MV|SH|GR)\d{8,}(\.[FS]\.EP)?<span class="math-inline">\|^\\d\{8,\}</span>")
    return bool(gracenote_pattern.match(tvg_id))

# UPDATED: Helper to find a better display name from a potentially long channel name
def get_clean_display_name(raw_channel_name, attributes):
    """
    Attempts to extract a clean, concise display name from a raw channel name string.
    Prioritizes tvc-guide-title, then specific parsing of the raw_channel_name, then truncation.
    """
    clean_name_candidate = raw_channel_name.strip()

    # 1. Prioritize 'tvc-guide-title' if present and not empty
    tvc_guide_title = attributes.get('tvc-guide-title', '').strip()
    if tvc_guide_title:
        return tvc_guide_title

    # --- Aggressive parsing of raw_channel_name string (the part after the last comma of EXTINF) ---

    # 2. Try to extract content from the first quoted string at the very beginning
    # E.g., "Pluto TV Trending Now",description...
    # This handles cases where the channel name itself starts and ends with quotes,
    # or where the meaningful part is the first quoted segment.
    match_initial_quoted = re.match(r'^["\']([^"\']+)["\']', clean_name_candidate)
    if match_initial_quoted:
        candidate = match_initial_quoted.group(1).strip()
        if candidate and len(candidate) < 60: # Ensure it's not an empty quote or excessively long
            return candidate

    # 3. Try to extract content before the first comma, if not handled by quotes above.
    # This specifically targets cases like "Channel Name, Some long description"
    if ',' in clean_name_candidate:
        first_segment = clean_name_candidate.split(',', 1)[0].strip()
        # Ensure this segment isn't just a stray quote or too short to be a name
        if first_segment and len(first_segment) > 2 and not re.match(r'^[\"\']<span class="math-inline">', first\_segment\)\:
\# Also, check if it already seems like the desired name before a description
\# \(e\.g\., "Pluto TV Trending Now", description\.\.\.\)\. Remove any remaining quotes\.
candidate \= re\.sub\(r'\[\\"\\'\]', '', first\_segment\)\.strip\(\)
if candidate\:
return candidate
\# 4\. Fallback\: Aggressive cleaning and truncation from the beginning
\# Remove all quotes first for consistent processing
clean\_name\_candidate \= re\.sub\(r'\[\\"\\'\]', '', clean\_name\_candidate\)\.strip\(\) 
\# Remove descriptions typically separated by "\-\-", "\:", " \- ", etc\.
\# Try longest separators first for clearer splits
clean\_name\_candidate \= re\.sub\(r'\\s\+\-\-\\s\+\.\*</span>', '', clean_name_candidate).strip()
    clean_name_candidate = re.sub(r'\s+-\s+.*<span class="math-inline">', '', clean\_name\_candidate\)\.strip\(\)
clean\_name\_candidate \= re\.sub\(r'\\s\*\:\\s\+\.\*</span>', '', clean_name_candidate).strip()

    # Remove content within parentheses or square brackets that often contain descriptions
    clean_name_candidate = re.sub(r'\s*\(.*\)<span class="math-inline">', '', clean\_name\_candidate\)\.strip\(\)
clean\_name\_candidate \= re\.sub\(r'\\s\*\\\[\.\*\\\]</span>', '', clean_name_candidate).strip()

    # Remove common trailing descriptive terms (case-insensitive)
    clean_name_candidate = re.sub(r'\s+(HD|SD|Live|TV|Channel|Show|Movie|Series|Now)\s*$', '', clean_name_candidate, flags=re.IGNORECASE).strip()

    # Truncate if still very long, add ellipsis
    if len(clean_name_candidate) > 50: # Slightly shorter target length for tvg-name
        clean_name_candidate = clean_name_candidate[:47].strip() + '...'

    # Final general cleanup for display purposes: remove non-essential punctuation
    clean_name_candidate = re.sub(r'[^\w\s.,&+\-:]', '', clean_name_candidate).strip() 

    return clean_name_candidate if clean_name_candidate else "Unknown Channel"


def check_m3u(file_content, mode='advanced'):
    """
    Parses M3U content, identifies errors/warnings, and suggests automated fixes based on mode.
    Mode: 'basic' for essential checks, 'advanced' for comprehensive checks.
    Returns (list_of_errors, list_of_channels, list_of_fix_suggestions).
    """
    errors = []
    channels = []
    fix_suggestions = []
    lines = file_content.splitlines()
    
    i = 0
    channel_count = 0
    tvg_id_map = {}
    channel_name_map = {}

    while i < len(lines):
        line_num_display = i + 1
        line = lines[i].strip()
        
        if not line and not (i > 0 and lines[i-1].strip().startswith('#EXTINF:')):
            i += 1
            continue

        if line.startswith('#EXTINF:'):
            channel_count += 1
            
            match = re.search(r'#EXTINF:(-?\d+)\s*([^,]*),(.*)', line)
            if not match:
                errors.append(f"M3U Error: Malformed EXTINF line (Line {line_num_display}): {line}. Expected '#EXTINF:<duration> [attributes],<channel name>'")
                i += 1
                continue

            duration = match.group(1)
            attributes_str = match.group(2) # The string containing all attributes (e.g., 'tvg-id="abc" tvg-name="def"')
            channel_name = match.group(3).strip() # The full raw channel name after the comma

            if not channel_name:
                errors.append(f"M3U Error: Channel name missing in EXTINF line (Line {line_num_display}): {line}")

            attributes = {}
            for attr_match in re.finditer(r'(\S+)="([^"]*)"', attributes_str):
                attributes[attr_match.group(1).lower()] = attr_match.group(2)

            current_line_attributes = attributes.copy()
            modified_attributes_for_fix = False

            tvg_id = attributes.get('tvg-id', '').strip()
            tvg_name = attributes.get('tvg-name', '').strip()
            group_title = attributes.get('group-title', '').strip()

            # Determine the suggested_display_name for tvg-name and potentially tvg-id
            suggested_display_name = get_clean_display_name(channel_name, attributes)

            # --- Basic Mode Checks & Fixes ---
            # Basic mode focuses ONLY on tvg-id and stream URL pairing.
            # It does not suggest tvg-name or group-title fixes.

            # Suggestion 1: Missing tvg-id (REQUIRED in basic mode for guide data)
            if not tvg_id:
                suggested_tvg_id = sanitize_channel_name_for_id(suggested_display_name)
                if suggested_tvg_id:
                    current_line_attributes['tvg-id'] = suggested_tvg_id
                    modified_attributes_for_fix = True
                    errors.append(f"M3U Channels DVR Warning: Channel '{channel_name}' (Line {line_num_display}) is missing 'tvg-id'. This is crucial for EPG matching in Channels DVR. Suggesting fix: Add tvg-id='{suggested_tvg_id}'.")
                else:
                    errors.append(f"M3U Channels DVR Warning: Channel '{channel_name}' (Line {line_num_display}) is missing 'tvg-id'. (Cannot auto-suggest a fix for this name).")
            
            # --- Advanced Mode Specific Checks & Fixes ---
            if mode == 'advanced':
                # Suggestion 2: Missing tvg-name (Use the derived suggested_display_name)
                if not tvg_name:
                    current_line_attributes['tvg-name'] = suggested_display_name
                    modified_attributes_for_fix = True
                    errors.append(f"M3U Channels DVR Warning: Channel '{channel_name}' (Line {line_num_display}) is missing 'tvg-name'. Channels DVR often uses this for display. Suggesting fix: Add tvg-name='{suggested_display_name}'.")
                
                # Suggestion 3: Missing group-title
                if not group_title:
                    suggested_group_title = "Unsorted"
                    current_line_attributes['group-title'] = suggested_group_title
                    modified_attributes_for_fix = True
                    errors.append(f"M3U Channels DVR Suggestion: Channel '{channel_name}' (Line {line_num_display}) is missing 'group-title'. Adding one helps organize channels in Channels DVR. Suggesting fix: Add group-title='{suggested_group_title}'.")

            # Add a single 'rebuild_extinf_attributes' fix if any attributes were modified in current mode
            if modified_attributes_for_fix:
                fix_suggestions.append({
                    'type': 'rebuild_extinf_attributes',
                    'line_num': line_num_display,
                    'duration': duration,
                    'channel_name': channel_name, # Original raw channel name for the reconstruction
                    'final_attributes': current_line_attributes
                })
            
            current_tvg_id_for_checks = current_line_attributes.get('tvg-id', '').strip()

            if current_tvg_id_for_checks:
                if current_tvg_id_for_checks in tvg_id_map:
                    errors.append(f"M3U Channels DVR Warning: Duplicate 'tvg-id' '{current_tvg_id_for_checks}' found for channel '{channel_name}' (Line {line_num_display}). Previous at line(s): {', '.join(map(str, tvg_id_map[current_tvg_id_for_checks]))}. Channels DVR may only import one instance.")
                    tvg_id_map[current_tvg_id_for_checks].append(line_num_display)
                else:
                    tvg_id_map[current_tvg_id_for_checks] = [line_num_display]

            if channel_name:
                if channel_name in channel_name_map:
                    errors.append(f"M3U Warning: Duplicate channel name '{channel_name}' found (Line {line_num_display}). Previous at line(s): {', '.join(map(str, channel_name_map[channel_name]))}. This might cause confusion.")
                    channel_name_map[channel_name].append(line_num_display)
                else:
                    channel_name_map[channel_name] = [line_num_display]

            stream_url = ""
            stream_url_found_at_line = -1
            
            for j in range(i + 1, len(lines)):
                temp_line = lines[j].strip()
                if not temp_line:
                    continue
                if temp_line.startswith('#EXTINF:') or temp_line.startswith('#EXTM3U'):
                    break
                if not temp_line.startswith('#'):
                    stream_url = temp_line
                    stream_url_found_at_line = j + 1
                    break
            
            # Stream URL check is always critical, regardless of mode
            if stream_url:
                if stream_url_found_at_line != (i + 2): 
                    fix_suggestions.append({
                        'type': 'reorder_stream_url',
                        'line_num': line_num_display,
                        'original_stream_line_num': stream_url_found_at_line,
                        'stream_url': stream_url,
                        'channel_name': channel_name
                    })
                    errors.append(f"M3U Error: Stream URL for channel '{channel_name}' (Line {line_num_display}) was not immediately after EXTINF line. Found at Line {stream_url_found_at_line}. Suggesting fix: Reorder URL.")
                
                i = stream_url_found_at_line - 1
            else:
                errors.append(f"M3U Error: Missing stream URL after EXTINF line for channel '{channel_name}' (Line {line_num_display}). Each #EXTINF must be immediately followed by a stream URL.")
                i += 1
            
            # This is a suggestion, only include in advanced mode
            if mode == 'advanced' and stream_url and not (stream_url.lower().endswith('.m3u8') or '.ts' in stream_url.lower() or '/hls/' in stream_url.lower()):
                errors.append(f"M3U Channels DVR Suggestion: Stream URL for '{channel_name}' (Line {line_num_display}) might not be HLS (.m3u8) or MPEG-TS (.ts). Channels DVR generally prefers HLS or raw MPEG-TS streams.")
            
            channels.append({
                'name': channel_name, # This remains the original full name
