-----

# Dockerized M3U/EPG File Checker

A powerful and user-friendly web tool designed to **validate, troubleshoot, and suggest automated fixes for M3U playlists and XMLTV EPG (Electronic Program Guide) files**, with a strong focus on compatibility with **Channels DVR**.

-----

## About the Project

Managing M3U playlists and XMLTV EPG data for personal media servers like Channels DVR can often lead to issues such as missing guide data, incorrect channel names, or stream playback problems. This project offers a simple, Dockerized web interface to help users quickly identify and resolve common pitfalls in their M3U and EPG files.

It acts as a **pre-flight checker and diagnostic tool**, offering insights and even suggesting automated corrections to streamline your Channels DVR setup.

-----

## Features

### Flexible Input Sources

  * **File Uploads:** Upload your M3U playlist (`.m3u`, `.m3u8`) and EPG XMLTV (`.xml`, `.xmltv`) files directly.
  * **URL Fetching:** Fetch data from publicly accessible URLs, which is convenient for regularly updated sources.

### Comprehensive M3U Playlist Analysis

  * Validates `#EXTINF` and stream URL pairing, flagging malformed entries.
  * Checks for missing or duplicate `tvg-id`, `tvg-name`, and `group-title` attributes.
  * Suggests improvements for stream URL formats (prefers HLS/MPEG-TS for Channels DVR).
  * Warns about excessive channel counts that might impact Channels DVR performance.

### Detailed EPG XMLTV File Analysis

  * Verifies XML syntax and structural integrity.
  * Identifies missing or duplicate channel IDs.
  * Checks for missing `display-name` for channels (essential for guide display).
  * Validates program start and stop times and detects overlaps.
  * Suggests missing program details (`title`, `description`, `series-id`, `episode-num`) crucial for Channels DVR's DVR features.

### M3U/EPG Cross-Compatibility Checks

  * Ensures `tvg-id` in M3U precisely matches `id` in EPG for accurate guide data linking.
  * Highlights unmatched channels in both files.

### Automated M3U Fix Suggestions (Beta)

  * Offers a beta feature to generate a corrected M3U file for download.
  * Currently includes fixes for: auto-adding `tvg-id` from channel names and reordering mispositioned stream URLs.
  * The generated file is available for preview and download, with a clear "Beta" disclaimer for user caution.

### Targeted Channels DVR Setup Advice

  * Provides a dedicated section for general best practices and troubleshooting tips specific to configuring custom M3U and EPG sources in Channels DVR. This includes important information about `tvg-id` consistency and Gracenote ID integration.

### User-Friendly Interface

  * Presents all analysis results, errors, warnings, and suggestions in a clear, categorized, and visually distinct format.
  * Utilizes collapsible sections for detailed analyses, keeping the initial view clean and focused on critical compatibility advice.
  * Employs scrollable containers for long lists of messages or detected channels, ensuring all information is accessible without overflowing the page.
  * Includes a custom favicon for enhanced branding.

-----

## Getting Started

These instructions will get you a copy of the project up and running on your local machine using Docker.

### Prerequisites

  * **Docker:** Ensure Docker is installed and running on your system. You can download it from [Docker's official website](https://www.docker.com/get-started/).

### Cloning the Repository

```bash
git clone https://github.com/nuken/m3u-epg-checker.git
cd m3u-epg-checker
```

### Running the Docker Container

```bash
docker run -d --restart unless-stopped --name m3u-epg-checker -p 5000:5000 rcvaughn2/m3u-epg-checker
```

Let's break down this command:

  * `-d`: Runs the container in **detached mode**, meaning it runs in the background.
  * `--restart unless-stopped`: Configures the container to automatically restart unless it's explicitly stopped. This is great for long-running services.
  * `--name m3u-epg-checker`: Assigns a friendly name to your container, making it easier to manage (e.g., `docker stop m3u-epg-checker`).
  * `-p 5000:5000`: Maps port 5000 on your host machine to port 5000 inside the Docker container. This is the port Flask uses.
  * `rcvaughn2/m3u-epg-checker`: The name of the Docker image to run. (Note: The `docker build` command uses `m3u-epg-checker` for your local build. If you push it to Docker Hub under `rcvaughn2/m3u-epg-checker`, then you'd use that name here).

After running this command, the checker will be accessible in your web browser at `http://localhost:5000`.

-----

## Usage

1.  Open your web browser and go to `http://localhost:5000`.
2.  You'll see options to either upload your M3U and/or EPG files or provide their public URLs.
3.  Click "Check Files" to initiate the analysis.
4.  The results page will display:
      * **M3U/EPG Compatibility Checks:** Critical issues related to how your M3U and EPG files work together for Channels DVR.
      * **Channels DVR Setup Advice & Best Practices:** General tips to optimize your Channels DVR setup.
      * **Automated M3U Fixes (Beta):** If applicable, a preview of your corrected M3U file and a download button.
      * **M3U Playlist Analysis (Collapsible):** Detailed errors, warnings, and detected channels from your M3U file.
      * **EPG XMLTV Analysis (Collapsible):** Detailed errors, warnings, and detected channels from your EPG file.

-----

## Automated Fixes (Beta)

The automated fixes feature is currently in beta. While it aims to correct common issues, **always review the generated file carefully** before using it in your Channels DVR or other media server. It may not perfectly address all edge cases, and manual review is recommended.

-----


## Acknowledgements

  * **Flask:** The micro web framework used.
  * **lxml:** For efficient XML parsing.
  * **Requests:** For handling HTTP requests.
  * **Channels DVR:** The inspiration for this checker.

-----
