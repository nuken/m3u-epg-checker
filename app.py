from flask import Flask, request, render_template, redirect, url_for, send_file
import re
from lxml import etree
import requests
from datetime import datetime, timedelta
import io # For in-memory file for download
from markupsafe import escape # For escaping HTML in Jinja2 templates
import json # For serializing fix suggestions to JSON

app = Flask(__name__)

# --- Helper function for fetching content ---
def fetch_content(source_type, source_value):
    """
    Fetches content from an uploaded file or a URL.
    Returns (content_string, list_of_errors).
    """
    if source_type == 'file':
        try:
            return source_value.read().decode('utf-8', errors='ignore'), [] # Return empty list on success
        except Exception as e:
            return None, [f"Error reading uploaded file: {e}"]
    elif source_type == 'url':
        try:
            response = requests.get(source_value, timeout=10) # 10-second timeout
            response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
            return response.text, [] # Return empty list on success
        except requests.exceptions.RequestException as e:
            return None, [f"Error fetching URL '{source_value}': {e}"]
        except Exception as e:
            return None, [f"An unexpected error occurred while fetching URL '{source_value}': {e}"]
    return None, ["Invalid source type provided."]

# --- M3U Parsing, Checking, and Fix Suggestion Functions ---

def sanitize_channel_name_for_id(name):
    """
    Sanitizes a channel name to be used as a tvg-id.
    Removes non-alphanumeric, replaces spaces/underscores with underscores, lowercase.
    """
    # Allow alphanumeric, spaces, and hyphens. Remove others.
    sane_name = re.sub(r'[^\w\s-]', '', name).strip()
    # Replace any sequence of spaces with a single underscore
    sane_name = re.sub(r'\s+', '_', sane_name)
    sane_name = sane_name.lower()
    return sane_name

def check_m3u(file_content):
    """
    Parses M3U content, identifies errors/warnings, and suggests automated fixes.
    Returns (list_of_errors, list_of_channels, list_of_fix_suggestions).
    """
    errors = []
    channels = []
    fix_suggestions = [] # New list to store fix suggestions
    lines = file_content.splitlines()
    original_lines = list(lines) # Keep original lines for potential modifications later in apply_m3u_fixes
    
    i = 0
    channel_count = 0 # For Channels DVR limit check
    tvg_id_map = {} # tvg_id -> [line_numbers] for duplicate checks
    channel_name_map = {} # channel_name -> [line_numbers] for duplicate checks

    while i < len(lines):
        line_num_display = i + 1 # For user display (1-indexed)
        line = lines[i].strip()
        
        # Skip empty lines, but only if they are not expected to be a stream URL.
        # This prevents skipping a legitimate stream URL if it was on a blank line after EXTINF.
        if not line and not (i > 0 and lines[i-1].strip().startswith('#EXTINF:')):
            i += 1
            continue

        if line.startswith('#EXTINF:'):
            channel_count += 1
            
            match = re.search(r'#EXTINF:(-?\d+)\s*([^,]*),(.*)', line)
            if not match:
                errors.append(f"M3U Error: Malformed EXTINF line (Line {line_num_display}): {line}. Expected '#EXTINF:<duration> [attributes],<channel name>'")
                i += 1 # Advance to next line
                continue # Skip to next loop iteration

            duration = match.group(1)
            attributes_str = match.group(2)
            channel_name = match.group(3).strip()

            if not channel_name:
                errors.append(f"M3U Error: Channel name missing in EXTINF line (Line {line_num_display}): {line}")

            attributes = {}
            for attr_match in re.finditer(r'(\S+)="([^"]*)"', attributes_str):
                attributes[attr_match.group(1).lower()] = attr_match.group(2)

            tvg_id = attributes.get('tvg-id', '').strip()
            tvg_name = attributes.get('tvg-name', '').strip()
            tvg_logo = attributes.get('tvg-logo', '').strip()
            group_title = attributes.get('group-title', '').strip()

            # --- M3U Channels DVR Specific Checks & Fix Suggestion 1: Missing tvg-id ---
            # Determine effective tvg_id for checks, including potential suggestion
            suggested_tvg_id = ""
            current_tvg_id_for_checks = tvg_id # Default to existing tvg_id
            
            if not tvg_id:
                suggested_tvg_id = sanitize_channel_name_for_id(channel_name)
                if suggested_tvg_id: # Only suggest if a sane ID can be generated
                    new_attributes_str = attributes_str
                    if new_attributes_str:
                        new_attributes_str += f' tvg-id="{suggested_tvg_id}"'
                    else:
                        new_attributes_str = f'tvg-id="{suggested_tvg_id}"'

                    fix_suggestions.append({
                        'type': 'add_tvg_id',
                        'line_num': line_num_display,
                        'original_line': line,
                        'channel_name': channel_name,
                        'suggested_tvg_id': suggested_tvg_id,
                        'new_extinf_line': f'#EXTINF:{duration} {new_attributes_str},{channel_name}'
                    })
                    errors.append(f"M3U Channels DVR Warning: Channel '{channel_name}' (Line {line_num_display}) is missing 'tvg-id'. This is crucial for EPG matching in Channels DVR. Suggesting fix: Add tvg-id='{suggested_tvg_id}'.")
                    current_tvg_id_for_checks = suggested_tvg_id # Use suggested ID for subsequent checks in this loop
                else: # Could not generate a sane ID from channel name
                    errors.append(f"M3U Channels DVR Warning: Channel '{channel_name}' (Line {line_num_display}) is missing 'tvg-id'. (Cannot auto-suggest a fix for this name).")

            if not tvg_name:
                errors.append(f"M3U Channels DVR Warning: Channel '{channel_name}' (Line {line_num_display}) is missing 'tvg-name'. Channels DVR often uses this for display.")
            if not group_title:
                errors.append(f"M3U Channels DVR Suggestion: Channel '{channel_name}' (Line {line_num_display}) is missing 'group-title'. Adding one helps organize channels in Channels DVR.")

            # Duplicate tvg-id check (using the current_tvg_id_for_checks)
            if current_tvg_id_for_checks: # Only check if we have a tvg_id (original or suggested)
                if current_tvg_id_for_checks in tvg_id_map:
                    errors.append(f"M3U Channels DVR Warning: Duplicate 'tvg-id' '{current_tvg_id_for_checks}' found for channel '{channel_name}' (Line {line_num_display}). Previous at line(s): {', '.join(map(str, tvg_id_map[current_tvg_id_for_checks]))}. Channels DVR may only import one instance.")
                    tvg_id_map[current_tvg_id_for_checks].append(line_num_display)
                else:
                    tvg_id_map[current_tvg_id_for_checks] = [line_num_display]

            # Duplicate channel name check (less critical but can confuse)
            if channel_name:
                if channel_name in channel_name_map:
                    errors.append(f"M3U Warning: Duplicate channel name '{channel_name}' found (Line {line_num_display}). Previous at line(s): {', '.join(map(str, channel_name_map[channel_name]))}. This might cause confusion.")
                    channel_name_map[channel_name].append(line_num_display)
                else:
                    channel_name_map[channel_name] = [line_num_display]

            # --- Fix Suggestion 2: Missing/Reordered Stream URL after EXTINF ---
            stream_url = ""
            stream_url_found_at_line = -1
            
            # Look for the stream URL on the next few lines
            # A common pattern is: EXTINF \n #EXTVLCOPT \n URL
            # We need to find the first line that is not a comment and not another EXTINF
            for j in range(i + 1, len(lines)):
                temp_line = lines[j].strip()
                if not temp_line: # Skip blank lines
                    continue
                if temp_line.startswith('#EXTINF:') or temp_line.startswith('#EXTM3U'): # New channel or end of playlist
                    break
                if not temp_line.startswith('#'): # Found a non-comment line, assume it's the URL
                    stream_url = temp_line
                    stream_url_found_at_line = j + 1
                    break
            
            if stream_url:
                # If the URL was found but not on the immediate next line (i+1 is the EXTINF line, i+2 is the expected URL line)
                if stream_url_found_at_line != (i + 2): 
                    fix_suggestions.append({
                        'type': 'reorder_stream_url',
                        'line_num': line_num_display, # <-- FIX: Changed from 'extinf_line_num' to 'line_num'
                        'original_stream_line_num': stream_url_found_at_line,
                        'stream_url': stream_url,
                        'channel_name': channel_name
                    })
                    errors.append(f"M3U Error: Stream URL for channel '{channel_name}' was not immediately after EXTINF line (Line {line_num_display}). Found at Line {stream_url_found_at_line}. Suggesting fix: Reorder URL.")
                
                # Advance main loop index to point to the line *after* the found stream URL
                i = stream_url_found_at_line - 1 # Since main loop increments i again
            else:
                errors.append(f"M3U Error: Missing stream URL after EXTINF line for channel '{channel_name}' (Line {line_num_display}). Each #EXTINF must be immediately followed by a stream URL.")
                i += 1 # Advance if no URL found to prevent infinite loop

            # Stream URL format check (if stream_url was found)
            if stream_url and not (stream_url.lower().endswith('.m3u8') or '.ts' in stream_url.lower() or '/hls/' in stream_url.lower()):
                errors.append(f"M3U Channels DVR Suggestion: Stream URL for '{channel_name}' (Line {line_num_display}) might not be HLS (.m3u8) or MPEG-TS (.ts). Channels DVR generally prefers HLS or raw MPEG-TS streams.")
            
            channels.append({
                'name': channel_name,
                'tvg_id': current_tvg_id_for_checks, # Store the potentially suggested tvg_id
                'tvg_name': tvg_name,
                'tvg_logo': tvg_logo,
                'group_title': group_title,
                'stream_url': stream_url
            })

        elif not line.startswith('#EXTM3U'):
            errors.append(f"M3U Warning: Unexpected line (might be ignored) (Line {line_num_display}): {line}")
        
        i += 1 # Advance loop index for the next iteration (unless already advanced within the EXTINF block)

    # Channels DVR Channel Limit Check
    if channel_count > 750:
        errors.append(f"M3U Channels DVR Warning: Detected {channel_count} channels. Channels DVR might experience performance issues or limits with more than ~750 channels per M3U playlist.")

    return errors, channels, fix_suggestions # Return fix_suggestions as well


# --- EPG Parsing and Checking Functions ---
def parse_xmltv_datetime(dt_str):
    """Parses XMLTV datetime string (YYYYMMDDHHMMSS +/-ZZZZ) into datetime object."""
    try:
        # Split into datetime part and timezone offset part
        dt_part = dt_str[:14]
        # tz_offset_part = dt_str[14:].strip() # Not strictly needed for parsing datetime object itself

        # Parse datetime part
        dt_obj = datetime.strptime(dt_part, '%Y%m%d%H%M%S')

        # For comparison purposes, simply parsing the time string is often enough.
        # If true timezone awareness is needed, pytz or zoneinfo would be used.
        return dt_obj
    except ValueError:
        return None


def check_epg(file_content):
    """
    Parses EPG XMLTV content and identifies errors/warnings.
    Returns (list_of_errors, dict_of_channels, list_of_programs).
    """
    errors = []
    channels = {} # To store channel data
    programs_by_channel = {} # To store program data, grouped by channel_id for overlap checks
    all_program_data = [] # For general reference if needed elsewhere (simplified)

    try:
        root = etree.fromstring(file_content.encode('utf-8'))
        # Check for root element
        if root.tag != 'tv':
            errors.append("EPG Error: Root element is not 'tv'. Expected '<tv>' tag.")

        # Parse Channels
        epg_channel_ids = set() # For duplicate ID check
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
                errors.append(f"EPG Channels DVR Warning: Channel '{channel_id}' missing 'display-name'. This is what Channels DVR displays as the channel name.")
            
            channels[channel_id] = {
                'display_names': [name.text for name in display_names if name.text],
                'icon': channel_element.find('icon').get('src') if channel_element.find('icon') is not None else None
            }
            programs_by_channel[channel_id] = [] # Initialize list for programs of this channel

        # Parse Programs
        for program_element in root.findall('programme'):
            channel_id = program_element.get('channel')
            start_time_str = program_element.get('start')
            stop_time_str = program_element.get('stop')

            # Get title early for better message context
            title_element = program_element.find('title')
            program_title = title_element.text if title_element is not None and title_element.text else 'Unknown Title'

            program_errors_local = [] # Errors specific to this program

            if not channel_id:
                program_errors_local.append("Program element missing 'channel' attribute.")
            
            start_dt, stop_dt = None, None
            if not start_time_str:
                program_errors_local.append("Missing 'start' time.")
            else:
                start_dt = parse_xmltv_datetime(start_time_str)
                if start_dt is None:
                    program_errors_local.append(f"Invalid 'start' time format: '{start_time_str}'. Expected БукмекерларMMDDHHMMSS +/-ZZZZ.")

            if not stop_time_str:
                program_errors_local.append("Missing 'stop' time.")
            else:
                stop_dt = parse_xmltv_datetime(stop_time_str)
                if stop_dt is None:
                    program_errors_local.append(f"Invalid 'stop' time format: '{stop_time_str}'. Expected БукмекерларMMDDHHMMSS +/-ZZZZ.")

            if start_dt and stop_dt and start_dt >= stop_dt:
                 program_errors_local.append(f"Start time ({start_time_str}) is equal to or after stop time ({stop_time_str}).")


            if title_element is None or not title_element.text:
                program_errors_local.append("Missing 'title'. Essential for guide display.")
            
            description_element = program_element.find('desc')
            if description_element is None or not description_element.text:
                program_errors_local.append("Suggestion: Missing 'desc' (description). Adds rich info to guide.")

            category_element = program_element.find('category')
            is_movie = (category_element is not None and category_element.text and category_element.text.lower() == 'movie')

            series_id_attr = program_element.get('series-id')
            if not series_id_attr and not is_movie: # Only suggest for non-movies
                program_errors_local.append("Suggestion: Missing 'series-id'. Crucial for grouping TV show recordings.")

            episode_num_element = program_element.find('episode-num')
            if (episode_num_element is None or not episode_num_element.text) and not is_movie: # Only suggest for non-movies
                program_errors_local.append("Suggestion: Missing 'episode-num'. Helps uniquely identify episodes.")

            # Append any local errors for this program to the main errors list
            if program_errors_local:
                # Consolidate messages for a single program
                consolidated_msg = f"EPG Program Error/Warning: Channel '{channel_id}' Program ('{program_title}' from {start_time_str or 'N/A'} to {stop_time_str or 'N/A'}): "
                consolidated_msg += "; ".join(program_errors_local)
                errors.append(consolidated_msg)


            # Store parsed program data for overlap checking
            if channel_id in programs_by_channel:
                programs_by_channel[channel_id].append({
                    'start_dt': start_dt,
                    'stop_dt': stop_dt,
                    'title': program_title, # Store for overlap message
                    'start_time_str': start_time_str, # Store for overlap message
                    'stop_time_str': stop_time_str # Store for overlap message
                })
            else:
                # This case means a program references a channel_id not defined in <channel> tags
                errors.append(f"EPG Error: Program references unknown channel ID '{channel_id}'. Channel not found in <channel> definitions.")

    except etree.XMLSyntaxError as e:
        errors.append(f"EPG XML Syntax Error: The EPG file is not well-formed XML: {e}")
    except Exception as e:
        errors.append(f"EPG General Error: An unexpected error occurred during EPG parsing: {e}")

    # After parsing all programs, check for overlaps
    for channel_id, progs in programs_by_channel.items():
        # Filter out programs with invalid start/stop times before sorting
        progs_valid_times = [p for p in progs if p['start_dt'] and p['stop_dt']]
        progs_sorted = sorted(progs_valid_times, key=lambda x: x['start_dt'])
        
        for i in range(len(progs_sorted) - 1):
            current_prog = progs_sorted[i]
            next_prog = progs_sorted[i+1]

            if current_prog['stop_dt'] > next_prog['start_dt']:
                errors.append(
                    f"EPG Channels DVR Warning: Overlapping programs for channel '{channel_id}': "
                    f"'{current_prog['title']}' ({current_prog['start_time_str']} - {current_prog['stop_time_str']}) "
                    f"overlaps with '{next_prog['title']}' ({next_prog['start_time_str']} - {next_prog['stop_time_str']}). "
                    f"Channels DVR might misinterpret guide data here."
                )
    
    # Optionally, still build all_program_data if it's used elsewhere
    for channel_id, progs in programs_by_channel.items():
        for prog in progs:
            all_program_data.append({
                'channel_id': channel_id,
                'start': prog['start_time_str'],
                'stop': prog['stop_time_str'],
                'title': prog['title']
            })

    return errors, channels, all_program_data


# --- M3U-EPG Compatibility Checker ---
def check_m3u_epg_compatibility(m3u_channels, epg_channels):
    """
    Checks compatibility between M3U and EPG data for Channels DVR,
    separating specific issues from general advice.
    Returns (list_of_compatibility_issues, list_of_channels_dvr_advice).
    """
    compatibility_issues = [] # Specific issues (warnings/errors)
    channels_dvr_advice = []   # General advice/best practices

    m3u_tvg_ids = {c['tvg_id'] for c in m3u_channels if c['tvg_id']}
    epg_channel_ids = set(epg_channels.keys())

    # 1. Channels in M3U without matching EPG data
    for m3u_channel in m3u_channels:
        if m3u_channel['tvg_id']:
            if m3u_channel['tvg_id'] not in epg_channel_ids:
                compatibility_issues.append(f"Compatibility Warning: M3U channel '{m3u_channel['name']}' (tvg-id: '{m3u_channel['tvg_id']}') has no matching EPG data found by 'tvg-id'. This channel might not show guide data in Channels DVR.")
        else:
            # This is already caught by check_m3u, but included here for completeness of compatibility issues
            pass # We rely on check_m3u to report missing tvg-id


    # 2. EPG channels without matching M3U data
    for epg_id in epg_channel_ids:
        if epg_id not in m3u_tvg_ids:
            display_names = epg_channels.get(epg_id, {}).get('display_names', ['N/A'])
            compatibility_issues.append(f"Compatibility Warning: EPG channel '{', '.join(display_names)}' (id: '{epg_id}') has no matching M3U channel via 'tvg-id'. This EPG data will not be used by Channels DVR.")

    # 3. General Channels DVR compatibility advice
    # These are general tips, not direct "errors" or "warnings" from parsing.
    channels_dvr_advice.append("For optimal guide data, ensure 'tvg-id' in M3U *exactly* matches 'id' in EPG (case-sensitive).")
    channels_dvr_advice.append("Missing or inconsistent 'tvg-id' attributes are the most common reason for guide data not showing up.")
    channels_dvr_advice.append("Duplicate 'tvg-id' values in M3U can cause unpredictable channel importing in Channels DVR.")
    channels_dvr_advice.append("Ensure your EPG file includes essential program details like `<title>`, `<desc>`, `series-id` (for TV shows), and `episode-num` for best DVR functionality.")
    channels_dvr_advice.append("Overlapping program times in EPG for a single channel can lead to incorrect guide display or recording issues.")
    channels_dvr_advice.append("Channels DVR prefers HLS (.m3u8) or raw MPEG-TS (.ts) streams. Other formats might have limited or no support.")
    channels_dvr_advice.append("Consider adding a 'group-title' to your M3U channels to organize them into categories in Channels DVR's UI.")
    channels_dvr_advice.append("Remember: Channels DVR *can* display guide data without an external EPG file if your M3U channels use `tvg-id`s that map to known Gracenote IDs. Otherwise, an external EPG source is required.")

    return compatibility_issues, channels_dvr_advice


# --- NEW: Function to apply M3U fixes ---
def apply_m3u_fixes(original_content, fix_suggestions):
    """
    Applies a list of fix suggestions to the original M3U content to generate a fixed version.
    Fixes are applied in reverse order of line number to prevent index shifting issues.
    """
    # Split original content into lines, keeping line endings
    fixed_lines_array = original_content.splitlines(keepends=True) 
    
    # Sort fix suggestions in reverse order of line number
    # This is CRUCIAL to avoid index errors when modifying a list of lines
    fix_suggestions_sorted = sorted(fix_suggestions, key=lambda x: x['line_num'], reverse=True)

    for fix in fix_suggestions_sorted:
        fix_type = fix['type']
        line_idx = fix['line_num'] - 1 # Convert to 0-indexed for list access

        if fix_type == 'add_tvg_id':
            # This involves replacing the EXTINF line with the new one containing tvg-id
            if line_idx < len(fixed_lines_array):
                fixed_lines_array[line_idx] = fix['new_extinf_line'] + "\n"
            
        elif fix_type == 'reorder_stream_url':
            extinf_line_idx = fix['line_num'] - 1 # <-- FIX: Now consistently 'line_num'
            original_stream_line_idx = fix['original_stream_line_num'] - 1
            stream_url = fix['stream_url'] + "\n"

            # If the stream URL is already directly after the EXTINF line, no action needed
            if original_stream_line_idx == extinf_line_idx + 1:
                continue

            # Remove the stream URL from its original position
            if original_stream_line_idx < len(fixed_lines_array):
                del fixed_lines_array[original_stream_line_idx]
            
            # Insert the stream URL after the EXTINF line
            # The index for insertion needs to account for any deletions that happened after this EXTINF
            # but before the original stream_url. Since we process in reverse, the EXTINF line
            # itself should not have shifted *relative to the end of the file*.
            if extinf_line_idx < len(fixed_lines_array): # Ensure EXTINF line still exists
                 fixed_lines_array.insert(extinf_line_idx + 1, stream_url)
            elif extinf_line_idx == len(fixed_lines_array): # Case where EXTINF is the last line
                 fixed_lines_array.append(stream_url)


    return "".join(fixed_lines_array)


# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    m3u_file = request.files.get('m3u_file')
    m3u_url = request.form.get('m3u_url')

    epg_file = request.files.get('epg_file')
    epg_url = request.form.get('epg_url')

    m3u_content = None
    m3u_errors = [] # Initialize as an empty list
    epg_content = None
    epg_errors = [] # Initialize as an empty list

    # Determine M3U source and fetch content
    if m3u_file and m3u_file.filename:
        if not m3u_file.filename.lower().endswith(('.m3u', '.m3u8')):
            m3u_errors.append("Invalid M3U file extension. Please upload a .m3u or .m3u8 file.")
        else:
            fetched_content, fetch_msgs = fetch_content('file', m3u_file)
            m3u_content = fetched_content
            m3u_errors.extend(fetch_msgs)
    elif m3u_url:
        fetched_content, fetch_msgs = fetch_content('url', m3u_url)
        m3u_content = fetched_content
        m3u_errors.extend(fetch_msgs)
    else:
        m3u_errors.append("No M3U file or URL provided.")


    # Determine EPG source and fetch content
    if epg_file and epg_file.filename:
        if not (epg_file.filename.lower().endswith('.xml') or epg_file.filename.lower().endswith('.xmltv')):
            epg_errors.append("Invalid EPG file extension. Please upload a .xml or .xmltv file.")
        else:
            fetched_content, fetch_msgs = fetch_content('file', epg_file)
            epg_content = fetched_content
            epg_errors.extend(fetch_msgs)
    elif epg_url:
        fetched_content, fetch_msgs = fetch_content('url', epg_url)
        epg_content = fetched_content
        epg_errors.extend(fetch_msgs)


    m3u_channels_data = []
    epg_channels_data = {}
    epg_programs_data = []
    m3u_epg_compat_issues = []
    channels_dvr_advice = []
    
    m3u_fix_suggestions = [] # New list for M3U fix suggestions
    fixed_m3u_content = None # Initialize as None

    # Only run M3U analysis if content was successfully fetched
    if m3u_content:
        m3u_errors_analysis, m3u_channels_data, m3u_fix_suggestions = check_m3u(m3u_content)
        m3u_errors.extend(m3u_errors_analysis)
        
        # Now apply fixes if suggestions exist
        if m3u_fix_suggestions:
            fixed_m3u_content = apply_m3u_fixes(m3u_content, m3u_fix_suggestions)
        else:
            fixed_m3u_content = None # No fixes to apply
    

    # Only run EPG analysis if content was successfully fetched
    if epg_content:
        epg_errors_analysis, epg_channels_data, epg_programs_data = check_epg(epg_content)
        epg_errors.extend(epg_errors_analysis)
    
    # Run compatibility checks and collect general advice
    if m3u_content and epg_content:
        m3u_epg_compat_issues, channels_dvr_advice = check_m3u_epg_compatibility(m3u_channels_data, epg_channels_data)
    elif m3u_content and (not epg_content and not epg_errors): # M3U provided, EPG not provided/failed
        m3u_epg_compat_issues.append("Compatibility Note: No EPG file or valid EPG URL was provided.")
        channels_dvr_advice.append("Channels DVR *can* display guide data without an external EPG source if your M3U channels use `tvg-id`s that map to known Gracenote IDs. Otherwise, an external EPG source is required.")
    elif epg_content and (not m3u_content and not m3u_errors): # EPG provided, M3U not provided/failed
        m3u_epg_compat_issues.append("Compatibility Note: No M3U file or valid M3U URL was provided.")
        channels_dvr_advice.append("EPG data alone is not sufficient for Channels DVR; it needs to be linked to channels in a playlist (via `tvg-id`).")

    # Convert fix_suggestions to JSON string for passing to template (for download)
    m3u_fix_suggestions_json = json.dumps(m3u_fix_suggestions)

    # Escape m3u_content for HTML (important if original_m3u_content is passed in hidden field)
    # Using escape() is critical if content might contain HTML-like characters
    m3u_content_escaped = escape(m3u_content) if m3u_content else ""


    return render_template('results.html',
                           m3u_errors=m3u_errors,
                           epg_errors=epg_errors,
                           m3u_channels=m3u_channels_data,
                           epg_channels=epg_channels_data,
                           m3u_epg_compat_issues=m3u_epg_compat_issues,
                           channels_dvr_advice=channels_dvr_advice,
                           fixed_m3u_content=fixed_m3u_content,
                           m3u_fix_suggestions_count=len(m3u_fix_suggestions),
                           # Pass original M3U content and fix suggestions for download route
                           original_m3u_content=m3u_content_escaped, 
                           m3u_fix_suggestions_json=m3u_fix_suggestions_json
                           )

# --- Download route for fixed M3U ---
@app.route('/download_fixed_m3u', methods=['POST'])
def download_fixed_m3u():
    """
    Handles the download of the fixed M3U file. Re-applies fixes for robustness.
    """
    original_m3u_content_for_download = request.form.get('original_m3u_content')
    fix_suggestions_json = request.form.get('fix_suggestions_json')

    if not original_m3u_content_for_download or not fix_suggestions_json:
        return "Error: Missing data for download. Please go back and try again.", 400
    
    try:
        # Unescape original_m3u_content if it was escaped by markupsafe.escape
        # `escape` replaces characters like & with &amp;. `unescape` reverses this.
        from html import unescape # Python's built-in unescape
        original_content_unescaped = unescape(original_m3u_content_for_download)

        # Convert fix_suggestions_json back to Python list of dicts
        fix_suggestions_for_download = json.loads(fix_suggestions_json)
        
        fixed_content = apply_m3u_fixes(original_content_unescaped, fix_suggestions_for_download)
        
        buffer = io.BytesIO(fixed_content.encode('utf-8'))
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype='application/x-mpegurl', # Standard MIME type for M3U playlists
            as_attachment=True,
            download_name='fixed_playlist.m3u' # Suggested filename for download
        )
    except json.JSONDecodeError:
        app.logger.error("Failed to decode fix_suggestions_json for download.")
        return "Error: Invalid fix data for download.", 500
    except Exception as e:
        app.logger.error(f"Error generating fixed M3U for download: {e}")
        return f"An internal error occurred while generating the fixed file: {e}", 500


if __name__ == '__main__':
    # Set a more appropriate host for Docker deployment
    app.run(debug=False, host='0.0.0.0')
