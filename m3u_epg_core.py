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
    
    gracenote_pattern = re.compile(r"^(EP|MV|SH|GR)\d{8,}(\.[FS]\.EP)?$|^\d{8,}$")
    return bool(gracenote_pattern.match(tvg_id))

# UPDATED: Helper to find a better display name from a potentially long channel name
def get_clean_display_name(raw_channel_name, attributes):
    """
    Attempts to extract a clean, concise display name from a raw channel name string.
    Prioritizes tvc-guide-title, then specific parsing of the raw_channel_name, then truncation.
    """
    clean_name = raw_channel_name.strip()

    # 1. Prioritize 'tvc-guide-title' if present and not empty
    tvc_guide_title = attributes.get('tvc-guide-title', '').strip()
    if tvc_guide_title:
        return tvc_guide_title

    # --- Aggressive parsing of raw_channel_name string ---

    # 2. Look for content before the *first* comma if the name appears structured like "Short Name",Long Description
    # This handles "Pluto TV Trending Now",there's always a film...
    if ',' in clean_name:
        first_part = clean_name.split(',', 1)[0].strip()
        # If the first part is enclosed in quotes, strip them
        if (first_part.startswith('"') and first_part.endswith('"')) or \
           (first_part.startswith("'") and first_part.endswith("'")):
            return first_part[1:-1].strip()
        # Otherwise, if it's reasonably short, it might be the clean name
        if len(first_part) < 50: # Heuristic: if first part is not super long
            return first_part

    # 3. Look for content inside any quotes within the whole string
    # This might catch names like 'Channel Name' within a longer string
    match_quoted = re.search(r'["\']([^"\']+)["\']', clean_name)
    if match_quoted:
        # If multiple quoted sections, prioritize the first or a reasonable length one.
        # For simplicity, we'll take the first quoted group found.
        candidate = match_quoted.group(1).strip()
        if candidate and len(candidate) < 60: # Ensure it's not a very long quoted description itself
            return candidate

    # 4. Fallback: Aggressive cleaning and truncation from the beginning
    # Remove all quotes first for consistent processing
    clean_name = re.sub(r'[\"\']', '', clean_name).strip() 

    # Remove descriptions typically separated by "--", ":", " - ", etc.
    # Try longest separators first for clearer splits
    clean_name = re.sub(r'\s+--\s+.*$', '', clean_name).strip()
    clean_name = re.sub(r'\s+-\s+.*$', '', clean_name).strip()
    clean_name = re.sub(r'\s*:\s+.*$', '', clean_name).strip()

    # Remove content within parentheses or square brackets
    clean_name = re.sub(r'\s*\(.*\)$', '', clean_name).strip()
    clean_name = re.sub(r'\s*\[.*\]$', '', clean_name).strip()

    # Remove common trailing terms like "HD", "SD", "Live" if they are standalone
    clean_name = re.sub(r'\s+(HD|SD|Live|TV|Channel)\s*$', '', clean_name, flags=re.IGNORECASE).strip()

    # Truncate if still very long, add ellipsis
    if len(clean_name) > 50: # Slightly shorter target length for tvg-name
        clean_name = clean_name[:47].strip() + '...'

    # Final general cleanup for display purposes
    clean_name = re.sub(r'[^\w\s.,&+\-:]', '', clean_name).strip() 

    return clean_name if clean_name else "Unknown Channel"


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
            # CORRECTED LINE HERE: Use attributes_str, not attributes_match.group(2)
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
                'name': channel_name, # This remains the original full name for record keeping
                'tvg_id': current_line_attributes.get('tvg-id', ''),
                'tvg_name': current_line_attributes.get('tvg-name', ''), # This will be the cleaned name
                'tvg_logo': current_line_attributes.get('tvg-logo', attributes.get('tvg-logo', '').strip()),
                'group_title': current_line_attributes.get('group-title', ''),
                'stream_url': stream_url
            })

        elif line.startswith('#EXTVLCOPT:'):
            pass # Explicitly ignore VLC options
        
        elif not line.startswith('#EXTM3U'):
            errors.append(f"M3U Warning: Unexpected line (might be ignored) (Line {line_num_display}): {line}")
        
        i += 1
    
    # Channel count warning applies to both modes as it's a Channels DVR performance consideration
    if channel_count > 750:
        errors.append(f"M3U Channels DVR Warning: Detected {channel_count} channels. Channels DVR might experience performance issues or limits with more than ~750 channels per M3U playlist.")

    return errors, channels, fix_suggestions

def apply_m3u_fixes(original_content, fix_suggestions):
    """
    Applies a list of fix suggestions to the original M3U content to generate a fixed version.
    Fixes are applied in reverse order of line number to prevent index shifting issues.
    """
    fixed_lines_array = original_content.splitlines(keepends=True) 
    fix_suggestions_sorted = sorted(fix_suggestions, key=lambda x: x['line_num'], reverse=True)

    for fix in fix_suggestions_sorted:
        fix_type = fix['type']
        line_idx = fix['line_num'] - 1

        if line_idx < 0 or line_idx >= len(fixed_lines_array):
            print(f"Warning: Attempted to apply fix at invalid line index {line_idx}. Skipping fix: {fix}")
            continue

        if fix_type == 'rebuild_extinf_attributes':
            duration = fix['duration']
            channel_name = fix['channel_name']
            final_attributes = fix['final_attributes']

            new_attributes_str = format_attributes_for_extinf(final_attributes)
            new_extinf_line_content = f'#EXTINF:{duration} {new_attributes_str},{channel_name}'
            fixed_lines_array[line_idx] = new_extinf_line_content + "\n"
            
        elif fix_type == 'reorder_stream_url':
            extinf_line_idx = fix['line_num'] - 1
            original_stream_line_idx = fix['original_stream_line_num'] - 1
            stream_url = fix['stream_url']

            line_after_extinf = fixed_lines_array[extinf_line_idx + 1].strip() if (extinf_line_idx + 1) < len(fixed_lines_array) else ""
            if line_after_extinf == stream_url.strip():
                continue

            if original_stream_line_idx < len(fixed_lines_array) and \
               fixed_lines_array[original_stream_line_idx].strip() == stream_url.strip():
                del fixed_lines_array[original_stream_line_idx]
            else:
                print(f"Warning: Stream URL for channel '{fix['channel_name']}' not found at expected original line {fix['original_stream_line_num']} during reorder fix. Attempting to insert only.")

            fixed_lines_array.insert(extinf_line_idx + 1, stream_url + "\n")

    return "".join(fixed_lines_array)

def parse_xmltv_datetime(dt_str):
    """Parses XMLTV datetime string (YYYYMMDDHHMMSS +/-ZZZZ) into datetime object."""
    try:
        match = re.match(r'(\d{14})\s*([+-]\d{4})?', dt_str)
        if match:
            dt_part = match.group(1)
            dt_obj = datetime.strptime(dt_part, '%Y%m%d%H%M%S')
            return dt_obj
        return None
    except ValueError:
        return None

def check_epg(file_content):
    """
    Parses EPG XMLTV content and identifies errors/warnings.
    Returns (list_of_errors, dict_of_channels, list_of_programs).
    """
    errors = []
    channels = {}
    programs_by_channel = {}
    all_program_data = []

    try:
        root = etree.fromstring(file_content.encode('utf-8'))
        if root.tag != 'tv':
            errors.append("EPG Error: Root element is not 'tv'. Expected '<tv>' tag.")

        epg_channel_ids = set()
        for channel_element in root.findall('channel'):
            channel_id = channel_element.get('id')
            if not channel_id:
                errors.append("EPG Error: Channel element missing 'id' attribute.")
                continue

            if channel_id in epg_channel_ids:
                errors.append(f"EPG Error: Duplicate 'channel id' '{channel_id}' found in EPG file. Each channel must have a unique ID.")
            epg_channel_ids.add(channel_id)

            display_names = channel_element.findall('display-name')
            if not display_names:
                errors.append(f"EPG Channels DVR Warning: Channel '{channel_id}' missing 'display-name'.")
            
            channels[channel_id] = {
                'display_names': [name.text for name in display_names if name.text],
                'icon': channel_element.find('icon').get('src') if channel_element.find('icon') is not None else None
            }
            programs_by_channel[channel_id] = []

        for program_element in root.findall('programme'):
            channel_id = program_element.get('channel')
            start_time_str = program_element.get('start')
            stop_time_str = program_element.get('stop')

            title_element = program_element.find('title')
            program_title = title_element.text if title_element is not None and title_element.text else 'Unknown Title'

            program_errors_local = []

            if not channel_id:
                program_errors_local.append("Program element missing 'channel' attribute.")
            
            start_dt, stop_dt = None, None
            if not start_time_str:
                program_errors_local.append("Missing 'start' time.")
            else:
                start_dt = parse_xmltv_datetime(start_time_str)
                if start_dt is None:
                    program_errors_local.append(f"Invalid 'start' time format: '{start_time_str}'.")

            if not stop_time_str:
                program_errors_local.append("Missing 'stop' time.")
            else:
                stop_dt = parse_xmltv_datetime(stop_time_str)
                if stop_dt is None:
                    program_errors_local.append(f"Invalid 'stop' time format: '{stop_time_str}'.")

            if start_dt and stop_dt and start_dt >= stop_dt:
                 program_errors_local.append(f"Start time ({start_time_str}) is equal to or after stop time ({stop_time_str}).")

            title_elements = program_element.findall('title')
            if not title_elements or not any(t.text for t in title_elements):
                program_errors_local.append("Missing 'title'. Essential for guide display.")
            
            description_elements = program_element.findall('desc')
            if not description_elements or not any(d.text for d in description_elements):
                program_errors_local.append("Suggestion: Missing 'desc' (description).")

            category_elements = program_element.findall('category')
            is_movie = any(c.text and c.text.lower() == 'movie' for c in category_elements)

            series_id_attr = program_element.get('series-id')
            if not series_id_attr and not is_movie:
                program_errors_local.append("Suggestion: Missing 'series-id'.")

            episode_num_elements = program_element.findall('episode-num')
            if (not episode_num_elements or not any(e.text for e in episode_num_elements)) and not is_movie:
                program_errors_local.append("Suggestion: Missing 'episode-num'.")

            if program_errors_local:
                consolidated_msg = f"EPG Program Error/Warning: Channel '{channel_id}' Program ('{program_title}' from {start_time_str or 'N/A'} to {stop_time_str or 'N/A'}): "
                consolidated_msg += "; ".join(program_errors_local)
                errors.append(consolidated_msg)

            if channel_id in programs_by_channel:
                programs_by_channel[channel_id].append({
                    'start_dt': start_dt,
                    'stop_dt': stop_dt,
                    'title': program_title,
                    'start_time_str': start_time_str,
                    'stop_time_str': stop_time_str
                })
            else:
                errors.append(f"EPG Error: Program references unknown channel ID '{channel_id}'.")

    except etree.XMLSyntaxError as e:
        errors.append(f"EPG XML Syntax Error: The EPG file is not well-formed XML: {e}")
    except Exception as e:
        errors.append(f"EPG General Error: An unexpected error occurred during EPG parsing: {e}")

    for channel_id, progs in programs_by_channel.items():
        progs_valid_times = [p for p in progs if p['start_dt'] and p['stop_dt']]
        progs_sorted = sorted(progs_valid_times, key=lambda x: x['start_dt'])
        
        for i in range(len(progs_sorted) - 1):
            current_prog = progs_sorted[i]
            next_prog = progs_sorted[i+1]

            if current_prog['stop_dt'] > next_prog['start_dt']:
                errors.append(
                    f"EPG Channels DVR Warning: Overlapping programs for channel '{channel_id}': "
                    f"'{current_prog['title']}' ({current_prog['start_time_str']} - {current_prog['stop_time_str']}) "
                    f"overlaps with '{next_prog['title']}' ({next_prog['start_time_str']} - {next_prog['stop_time_str']})."
                )
    
    for channel_id, progs in programs_by_channel.items():
        for prog in progs:
            all_program_data.append({
                'channel_id': channel_id,
                'start': prog['start_time_str'],
                'stop': prog['stop_time_str'],
                'title': prog['title']
            })

    return errors, channels, all_program_data
