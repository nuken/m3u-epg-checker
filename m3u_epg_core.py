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
    """
    Formats a dictionary of attributes into a string for an EXTINF line.
    Ensures values are properly quoted, escaping internal quotes if necessary.
    """
    formatted_attrs = []
    for key in sorted(attributes_dict.keys()):
        value = attributes_dict[key]
        if value is not None and str(value).strip() != "":
            # Ensure values with spaces or special chars are re-quoted
            if ' ' in value or '"' in value or "'" in value or ',' in value:
                # Replace internal double quotes with escaped double quotes
                value_escaped = value.replace('"', '\\"')
                formatted_attrs.append(f'{key}="{value_escaped}"')
            else:
                # For simple values, just quote it
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

def get_clean_display_name(raw_channel_name_after_comma, attributes):
    """
    Attempts to extract a clean, concise display name for tvg-name.
    Prioritizes:
    1. 'tvg-name' attribute from the M3U line's attributes, with cleaning.
    2. 'tvc-guide-title' attribute from the M3U line's attributes.
    3. Parsing the 'raw_channel_name_after_comma' (text after the last comma of #EXTINF)
       for a clean title if the above are not available.
    """
    clean_name_candidate = raw_channel_name_after_comma.strip()

    # 1. Prioritize and clean 'tvg-name' from the parsed attributes dictionary
    tvg_name_from_attrs = attributes.get('tvg-name', '').strip()
    if tvg_name_from_attrs:
        # Check if the extracted tvg_name_from_attrs contains an unexpected comma.
        # This addresses cases like tvg-name="Name",description where the parsing regex
        # might have incorrectly included the description as part of tvg-name.
        if ',' in tvg_name_from_attrs:
            cleaned_tvg_name = tvg_name_from_attrs.split(',', 1)[0].strip()
            # If after splitting, the name looks reasonable and is not empty, use it.
            if cleaned_tvg_name and len(cleaned_tvg_name) < 60 and not re.fullmatch(r'\d+', cleaned_tvg_name):
                return cleaned_tvg_name
        else:
            # If no comma, and the name looks reasonable, use it directly.
            if tvg_name_from_attrs and len(tvg_name_from_attrs) < 60 and not re.fullmatch(r'\d+', tvg_name_from_attrs):
                return tvg_name_from_attrs


    # 2. Prioritize 'tvc-guide-title' from the parsed attributes dictionary
    tvc_guide_title = attributes.get('tvc-guide-title', '').strip()
    if tvc_guide_title:
        return tvc_guide_title

    # --- Fallback to parsing the raw_channel_name_after_comma string ---
    # This is the text after the last comma on the EXTINF line.

    # 3. Try to extract content from the first quoted string at the very beginning
    # E.g., "Pluto TV Trending Now",description...
    match_initial_quoted = re.match(r'^["\']([^"\']+)["\']', clean_name_candidate)
    if match_initial_quoted:
        candidate = match_initial_quoted.group(1).strip()
        if candidate and len(candidate) < 60:
            return candidate

    # 4. Try to extract content before the first comma in clean_name_candidate
    # This targets cases like "Channel Name, Some long description" where the name is first.
    if ',' in clean_name_candidate:
        first_segment = clean_name_candidate.split(',', 1)[0].strip()
        # Ensure this segment isn't just a stray quote or too short to be a name
        if first_segment and len(first_segment) > 2 and not re.match(r'^[\"\']$', first_segment):
            candidate = re.sub(r'[\"\']', '', first_segment).strip()
            if candidate:
                return candidate
    
    # 5. Ultimate Fallback: Aggressive cleaning and truncation of raw_channel_name_after_comma
    # Remove all quotes first for consistent processing
    clean_name_candidate = re.sub(r'[\"\']', '', clean_name_candidate).strip() 

    # Remove descriptions typically separated by "--", ":", " - ", etc.
    clean_name_candidate = re.sub(r'\s+--\s+.*$', '', clean_name_candidate).strip()
    clean_name_candidate = re.sub(r'\s+-\s+.*$', '', clean_name_candidate).strip()
    clean_name_candidate = re.sub(r'\s*:\s+.*$', '', clean_name_candidate).strip()

    # Remove content within parentheses or square brackets (often descriptions)
    clean_name_candidate = re.sub(r'\s*\(.*\)$', '', clean_name_candidate).strip()
    clean_name_candidate = re.sub(r'\s*\[.*\]$', '', clean_name_candidate).strip()

    # Remove common trailing descriptive terms (case-insensitive)
    clean_name_candidate = re.sub(r'\s+(HD|SD|Live|TV|Channel|Show|Movie|Series|Now)\s*$', '', clean_name_candidate, flags=re.IGNORECASE).strip()

    # Truncate if still very long, add ellipsis
    if len(clean_name_candidate) > 50:
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
        
        # Skip empty lines, but only if they are not expected to be a stream URL.
        if not line and not (i > 0 and lines[i-1].strip().startswith('#EXTINF:')):
            i += 1
            continue

        if line.startswith('#EXTINF:'):
            channel_count += 1
            
            # --- REVERTED TO A MORE ROBUST REGEX FOR EXTINF LINE PARSING ---
            # This regex splits the line into duration, attributes string, and raw channel name
            # by looking for the FIRST comma after the duration and attributes.
            # This is generally more stable for common M3U variations.
            # `(-?\d+)`: Duration (group 1)
            # `\s*`: Optional whitespace
            # `([^,]*?)`: Non-greedy match for attributes (group 2). This captures up to the FIRST comma.
            # `,`: The comma separator
            # `(.*)`: The rest of the line as raw_channel_name (group 3)
            # NOTE: If an attribute VALUE itself contains an unescaped comma before the "real" channel name comma,
            # this regex will incorrectly split `attributes_str`. This is a common M3U parsing challenge.
            # However, it's more stable than the previous complex regex for the overall line structure.
            match = re.search(r'#EXTINF:(-?\d+)\s*([^,]*),(.*)', line)
            
            if not match:
                errors.append(f"M3U Error: Malformed EXTINF line (Line {line_num_display}): {line}. Expected '#EXTINF:<duration> [attributes],<channel name>'")
                i += 1
                continue

            duration = match.group(1)
            attributes_str = match.group(2).strip() # The string containing all attributes
            raw_channel_name_after_comma = match.group(3).strip() # The full raw channel name AFTER the last comma

            if not raw_channel_name_after_comma:
                errors.append(f"M3U Error: Channel name missing in EXTINF line (Line {line_num_display}): {line}")

            attributes = {}
            # This part correctly parses key="value" pairs from the isolated attributes_str
            # It's robust to spaces within quoted values.
            for attr_match in re.finditer(r'(\S+)="([^"]*)"', attributes_str):
                attributes[attr_match.group(1).lower()] = attr_match.group(2)

            current_line_attributes = attributes.copy()
            modified_attributes_for_fix = False

            tvg_id = attributes.get('tvg-id', '').strip()
            tvg_name_from_attrs = attributes.get('tvg-name', '').strip() # Get current tvg-name attribute value
            group_title = attributes.get('group-title', '').strip()

            # Determine the suggested_display_name using the new logic.
            # Pass the raw_channel_name_after_comma (the text after the last comma)
            # and the fully parsed attributes for the best possible name extraction.
            suggested_display_name = get_clean_display_name(raw_channel_name_after_comma, attributes)

            # --- Basic Mode Checks & Fixes ---
            # Basic mode focuses ONLY on tvg-id and stream URL pairing.
            # It does not suggest tvg-name or group-title fixes.

            # Suggestion 1: Missing tvg-id (REQUIRED in basic mode for guide data)
            if not tvg_id:
                suggested_tvg_id = sanitize_channel_name_for_id(suggested_display_name)
                if suggested_tvg_id:
                    current_line_attributes['tvg-id'] = suggested_tvg_id
                    modified_attributes_for_fix = True
                    errors.append(f"M3U Channels DVR Warning: Channel '{raw_channel_name_after_comma}' (Line {line_num_display}) is missing 'tvg-id'. This is crucial for EPG matching in Channels DVR. Suggesting fix: Add tvg-id='{suggested_tvg_id}'.")
                else:
                    errors.append(f"M3U Channels DVR Warning: Channel '{raw_channel_name_after_comma}' (Line {line_num_display}) is missing 'tvg-id'. (Cannot auto-suggest a fix for this name).")
            
            # --- Advanced Mode Specific Checks & Fixes ---
            if mode == 'advanced':
                # Suggestion 2: Missing or incorrect tvg-name
                # Conditions for suggesting a tvg-name fix:
                # 1. tvg-name attribute is completely missing (empty string).
                # OR
                # 2. tvg-name attribute exists, but its value is different from our suggested_display_name,
                #    AND our suggested_display_name is valid (not "Unknown Channel"),
                #    AND the existing tvg-name attribute looks "bad" (e.g., purely numeric, very long, contains internal commas/quotes/descriptions).
                
                is_existing_tvg_name_potentially_bad = (
                    re.fullmatch(r'\d+', tvg_name_from_attrs) is not None or # Purely numeric (like "115455")
                    len(tvg_name_from_attrs) > 50 or # Excessively long
                    ',' in tvg_name_from_attrs or # Contains an internal comma
                    '\"' in tvg_name_from_attrs or '\'' in tvg_name_from_attrs or # Contains internal quotes
                    re.search(r'\s+--\s+.*$', tvg_name_from_attrs) is not None or # Contains common description separator
                    re.search(r'\s*:\s+.*$', tvg_name_from_attrs) is not None or # Contains common description separator
                    re.search(r'\s*\([^\)]*\)$', tvg_name_from_attrs) is not None # Contains parentheses (like "(HD)" or "(Description)")
                )

                if not tvg_name_from_attrs or \
                   (tvg_name_from_attrs != suggested_display_name and 
                    suggested_display_name != "Unknown Channel" and 
                    is_existing_tvg_name_potentially_bad):
                    
                    current_line_attributes['tvg-name'] = suggested_display_name
                    modified_attributes_for_fix = True
                    if not tvg_name_from_attrs:
                         errors.append(f"M3U Channels DVR Warning: Channel '{raw_channel_name_after_comma}' (Line {line_num_display}) is missing 'tvg-name'. Channels DVR often uses this for display. Suggesting fix: Add tvg-name='{suggested_display_name}'.")
                    else:
                         errors.append(f"M3U Channels DVR Warning: Channel '{raw_channel_name_after_comma}' (Line {line_num_display}) has an unclean 'tvg-name' attribute ('{tvg_name_from_attrs}'). Suggesting fix: Change tvg-name to '{suggested_display_name}'.")

                # Suggestion 3: Missing group-title
                if not group_title:
                    suggested_group_title = "Unsorted"
                    current_line_attributes['group-title'] = suggested_group_title
                    modified_attributes_for_fix = True
                    errors.append(f"M3U Channels DVR Suggestion: Channel '{raw_channel_name_after_comma}' (Line {line_num_display}) is missing 'group-title'. Adding one helps organize channels in Channels DVR. Suggesting fix: Add group-title='{suggested_group_title}'.")

            # Add a single 'rebuild_extinf_attributes' fix if any attributes were modified in current mode
            if modified_attributes_for_fix:
                fix_suggestions.append({
                    'type': 'rebuild_extinf_attributes',
                    'line_num': line_num_display,
                    'duration': duration,
                    'channel_name': raw_channel_name_after_comma, # Original raw channel name for reconstruction
                    'final_attributes': current_line_attributes
                })
            
            current_tvg_id_for_checks = current_line_attributes.get('tvg-id', '').strip()

            if current_tvg_id_for_checks:
                if current_tvg_id_for_checks in tvg_id_map: # Fixed typo: changed tvg_for_checks to current_tvg_id_for_checks
                    errors.append(f"M3U Channels DVR Warning: Duplicate 'tvg-id' '{current_tvg_id_for_checks}' found for channel '{raw_channel_name_after_comma}' (Line {line_num_display}). Previous at line(s): {', '.join(map(str, tvg_id_map[current_tvg_id_for_checks]))}. Channels DVR may only import one instance.")
                    tvg_id_map[current_tvg_id_for_checks].append(line_num_display)
                else:
                    tvg_id_map[current_tvg_id_for_checks] = [line_num_display]

            if raw_channel_name_after_comma:
                if raw_channel_name_after_comma in channel_name_map:
                    errors.append(f"M3U Warning: Duplicate channel name '{raw_channel_name_after_comma}' found (Line {line_num_display}). Previous at line(s): {', '.join(map(str, channel_name_map[raw_channel_name_after_comma]))}. This might cause confusion.")
                    channel_name_map[raw_channel_name_after_comma].append(line_num_display)
                else:
                    channel_name_map[raw_channel_name_after_comma] = [line_num_display]

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
                        'channel_name': raw_channel_name_after_comma
                    })
                    errors.append(f"M3U Error: Stream URL for channel '{raw_channel_name_after_comma}' (Line {line_num_display}) was not immediately after EXTINF line. Found at Line {stream_url_found_at_line}. Suggesting fix: Reorder URL.")
                
                i = stream_url_found_at_line - 1
            else:
                errors.append(f"M3U Error: Missing stream URL after EXTINF line for channel '{raw_channel_name_after_comma}' (Line {line_num_display}). Each #EXTINF must be immediately followed by a stream URL.")
                i += 1
            
            # This is a suggestion, only include in advanced mode
            if mode == 'advanced' and stream_url and not (stream_url.lower().endswith('.m3u8') or '.ts' in stream_url.lower() or '/hls/' in stream_url.lower()):
                errors.append(f"M3U Channels DVR Suggestion: Stream URL for '{raw_channel_name_after_comma}' (Line {line_num_display}) might not be HLS (.m3u8) or MPEG-TS (.ts). Channels DVR generally prefers HLS or raw MPEG-TS streams.")
            
            channels.append({
                'name': raw_channel_name_after_comma, # This remains the original full name for record keeping
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
            channel_name_after_comma = fix['channel_name'] # This is the original raw channel name AFTER the last comma
            final_attributes = fix['final_attributes']

            new_attributes_str = format_attributes_for_extinf(final_attributes)
            # Reconstruct the line: #EXTINF:duration attributes,channel_name_after_comma
            new_extinf_line_content = f'#EXTINF:{duration} {new_attributes_str},{channel_name_after_comma}'
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
