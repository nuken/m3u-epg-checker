from flask import Flask, request, render_template, redirect, url_for, send_file, abort
import uuid
import io

# Import the core logic functions
from m3u_epg_core import (
    fetch_content, 
    check_m3u, # This check_m3u will now accept a 'mode' argument
    apply_m3u_fixes, 
    check_epg,
    is_gracenote_id
)

app = Flask(__name__)

# --- Temporary storage for fixed files ---
app.temp_fixed_files = {} # Stores {file_id: bytes_content}

# --- M3U-EPG Compatibility Checker (remains in app.py as it uses both types of data) ---
def check_m3u_epg_compatibility(m3u_channels, epg_channels):
    """
    Checks compatibility between M3U and EPG data for Channels DVR,
    separating specific issues from general advice.
    Returns (list_of_compatibility_issues, list_of_channels_dvr_advice).
    """
    compatibility_issues = []
    channels_dvr_advice = []

    m3u_tvg_ids = {c['tvg_id'] for c in m3u_channels if c['tvg_id']}
    epg_channel_ids = set(epg_channels.keys())

    # Flag to track if any Gracenote IDs were found when EPG is missing
    gracenote_ids_found_without_epg = False

    # 1. Channels in M3U without matching EPG data
    for m3u_channel in m3u_channels:
        if m3u_channel['tvg_id']:
            if m3u_channel['tvg_id'] not in epg_channel_ids:
                compatibility_issues.append(f"Compatibility Warning: M3U channel '{m3u_channel['name']}' (tvg-id: '{m3u_channel['tvg_id']}') has no matching EPG data found by 'tvg-id'. This channel might not show guide data in Channels DVR.")
                
                # If no EPG was provided AND this tvg-id looks like Gracenote
                if not epg_channels and is_gracenote_id(m3u_channel['tvg_id']):
                    gracenote_ids_found_without_epg = True
        else:
            pass 


    # 2. EPG channels without matching M3U data
    for epg_id in epg_channel_ids:
        if epg_id not in m3u_tvg_ids:
            display_names = epg_channels.get(epg_id, {}).get('display_names', ['N/A'])
            compatibility_issues.append(f"Compatibility Warning: EPG channel '{', '.join(display_names)}' (id: '{epg_id}') has no matching M3U channel via 'tvg-id'. This EPG data will not be used by Channels DVR.")

    # 3. General Channels DVR compatibility advice (and specific notes for missing EPG)
    if not epg_channels and m3u_channels: # Only add this note if no EPG was successfully parsed but M3U channels exist
        if gracenote_ids_found_without_epg:
             compatibility_issues.append("Compatibility Note: No external EPG data provided. However, some M3U channels have `tvg-id`s that appear to be Gracenote IDs. Channels DVR might use its internal Gracenote guide data for these channels.")
        else:
            compatibility_issues.append("Compatibility Note: No external EPG data provided. Channels DVR will require `tvg-id`s that map to known Gracenote IDs to display guide data, or an external EPG source.")
    elif not m3u_channels and not epg_channels: # Neither M3U nor EPG processed
        # This case is usually handled by initial M3U errors if no data given.
        # But if no M3U data leads to no channels, and no EPG, this covers it.
        if not m3u_errors and not epg_errors: # Avoid redundancy if explicit errors already exist
            compatibility_issues.append("Compatibility Note: No M3U or EPG data provided for compatibility check.")


    channels_dvr_advice.append("For optimal guide data, ensure 'tvg-id' in M3U *exactly* matches 'id' in EPG (case-sensitive).")
    channels_dvr_advice.append("Missing or inconsistent 'tvg-id' attributes are the most common reason for guide data not showing up.")
    channels_dvr_advice.append("Duplicate 'tvg-id' values in M3U can cause unpredictable channel importing in Channels DVR.")
    channels_dvr_advice.append("Ensure your EPG file includes essential program details like `<title>`, `<desc>`, `series-id` (for TV shows), and `episode-num` for best DVR functionality.")
    channels_dvr_advice.append("Overlapping program times in EPG for a single channel can lead to incorrect guide display or recording issues.")
    channels_dvr_advice.append("Channels DVR prefers HLS (.m3u8) or raw MPEG-TS (.ts) streams. Other formats might have limited or no support.")
    channels_dvr_advice.append("Consider adding a 'group-title' to your M3U channels to organize them into categories in Channels DVR's UI.")
    channels_dvr_advice.append("Remember: Channels DVR *can* display guide data without an external EPG file if your M3u channels use `tvg-id`s that map to known Gracenote IDs. Otherwise, an external EPG source is required.")

    return compatibility_issues, channels_dvr_advice


# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    # Retrieve mode selection
    mode = request.form.get('mode', 'basic') # Default to 'basic' if not provided

    # Retrieve all potential input sources
    m3u_text_data = request.form.get('m3u_text_data')
    m3u_file = request.files.get('m3u_file')
    m3u_url = request.form.get('m3u_url')

    epg_text_data = request.form.get('epg_text_data')
    epg_file = request.files.get('epg_file')
    epg_url = request.form.get('epg_url')

    m3u_content = None
    m3u_errors = []
    epg_content = None
    epg_errors = []
    
    # --- Determine M3U source and fetch content based on precedence ---
    if m3u_text_data and m3u_text_data.strip():
        m3u_content = m3u_text_data.strip()
        if not m3u_content.startswith('#EXTM3U'):
            m3u_errors.append("M3U Warning: Pasted M3U text does not start with '#EXTM3U'. This may indicate malformed data.")
    elif m3u_file and m3u_file.filename:
        if not m3u_file.filename.lower().endswith(('.m3u', '.m3u8')):
            m3u_errors.append("Invalid M3U file extension. Please upload a .m3u or .m3u8 file.")
        else:
            fetched_content, fetch_msgs = fetch_content('file', m3u_file)
            m3u_content = fetched_content
            m3u_errors.extend(fetch_msgs)
    elif m3u_url and m3u_url.strip():
        fetched_content, fetch_msgs = fetch_content('url', m3u_url)
        m3u_content = fetched_content
        m3u_errors.extend(fetch_msgs)
    else:
        m3u_errors.append("No M3U data (text, file, or URL) provided.")

    # --- Determine EPG source and fetch content based on precedence ---
    if epg_text_data and epg_text_data.strip():
        epg_content = epg_text_data.strip()
        if not epg_content.startswith('<?xml') and not epg_content.startswith('<tv>'):
             epg_errors.append("EPG Warning: Pasted EPG text does not start with '<?xml' or '<tv>'. This may indicate malformed data.")
    elif epg_file and epg_file.filename:
        if not (epg_file.filename.lower().endswith('.xml') or epg_file.filename.lower().endswith('.xmltv')):
            epg_errors.append("Invalid EPG file extension. Please upload a .xml or .xmltv file.")
        else:
            fetched_content, fetch_msgs = fetch_content('file', epg_file)
            epg_content = fetched_content
            epg_errors.extend(fetch_msgs)
    elif epg_url and epg_url.strip():
        fetched_content, fetch_msgs = fetch_content('url', epg_url)
        epg_content = fetched_content
        epg_errors.extend(fetch_msgs)
    # EPG is optional, so no 'else' error for missing EPG


    m3u_channels_data = []
    epg_channels_data = {}
    epg_programs_data = []
    m3u_epg_compat_issues = []
    channels_dvr_advice = []
    
    m3u_fix_suggestions = []
    fixed_m3u_content = None
    fixed_file_id = None # Initialize file ID

    # Only run M3U analysis if content was successfully fetched
    if m3u_content:
        # Pass the selected mode to check_m3u
        m3u_errors_analysis, m3u_channels_data, m3u_fix_suggestions = check_m3u(m3u_content, mode) 
        m3u_errors.extend(m3u_errors_analysis)
        
        # Now apply fixes if suggestions exist
        if m3u_fix_suggestions:
            fixed_m3u_content = apply_m3u_fixes(m3u_content, m3u_fix_suggestions)
            # Store fixed content temporarily and generate an ID
            fixed_file_id = str(uuid.uuid4())
            app.temp_fixed_files[fixed_file_id] = fixed_m3u_content.encode('utf-8')
        else:
            fixed_m3u_content = m3u_content # If no fixes, the "fixed" content is just the original
    

    # Only run EPG analysis if content was successfully fetched
    if epg_content:
        epg_errors_analysis, epg_channels_data, epg_programs_data = check_epg(epg_content)
        epg_errors.extend(epg_errors_analysis)
    
    # Run compatibility checks and collect general advice
    m3u_epg_compat_issues, channels_dvr_advice = check_m3u_epg_compatibility(m3u_channels_data, epg_channels_data)
    
    return render_template('results.html',
                           m3u_errors=m3u_errors,
                           epg_errors=epg_errors,
                           m3u_channels=m3u_channels_data,
                           epg_channels=epg_channels_data,
                           m3u_epg_compat_issues=m3u_epg_compat_issues,
                           channels_dvr_advice=channels_dvr_advice,
                           fixed_m3u_content=fixed_m3u_content, # For display in <pre> tag
                           m3u_fix_suggestions_count=len(m3u_fix_suggestions),
                           fixed_file_id=fixed_file_id # Pass the file ID for download
                           )

# --- Download route for fixed M3U ---
@app.route('/download_fixed_m3u/<file_id>', methods=['GET'])
def download_fixed_m3u(file_id):
    """
    Handles the download of the fixed M3U file using a unique ID.
    """
    fixed_content_bytes = app.temp_fixed_files.get(file_id)

    if fixed_content_bytes is None:
        app.logger.error(f"Attempted to download non-existent file_id: {file_id}")
        return abort(404, description="File not found or expired.")
    
    try:
        buffer = io.BytesIO(fixed_content_bytes)
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype='application/x-mpegurl', # Standard MIME type for M3U playlists
            as_attachment=True,
            download_name='fixed_playlist.m3u' # Suggested filename for download
        )
    except Exception as e:
        app.logger.error(f"An internal error occurred while generating the fixed file for download (ID: {file_id}): {e}")
        return f"An internal error occurred while generating the fixed file: {e}", 500


if __name__ == '__main__':
    # Set a more appropriate host for Docker deployment
    app.run(debug=False, host='0.0.0.0')
