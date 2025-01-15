import gradio as gr
import json
import math
import os
import shutil
from auto_foley import run_auto_foley as af
from datetime import datetime
from gradio_vistimeline import VisTimeline

TIMELINE_ID = "editor-tab-timeline"
OUTPUT_VIDEO_ID = "output-video-player"
TRACK_LENGTH_ID = "track-length-item"

# --- Demo specific helper functions ---
def parse_date_to_milliseconds(date):
    if isinstance(date, int):   # Input is already in milliseconds (Unix timestamp)
        return date
    elif isinstance(date, str): # Input is ISO8601 datetime string
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        epoch = datetime(1970, 1, 1, tzinfo=dt.tzinfo)
        return int((dt - epoch).total_seconds() * 1000)
    else:
        return 0

def parse_frame_to_timestamp(frame, framerate):
    # Convert frame number with given framerate to corresponding time in milliseconds
    exact_ms = (frame / framerate) * 1000
    # Round up to nearest 50ms, which is the smallest time step on the maximum zoom of our timeline
    rounded_ms = math.ceil(exact_ms / 50) * 50
    return int(rounded_ms)

def format_video_info(video_info):
    if video_info is None:
        return ""
    
    label_names = {
        "Width": "Width",
        "Height": "Height",
        "Duration": "Length in seconds",
        "FrameCount": "Number of frames",
        "FrameRate": "Frame rate (fps)",
        "FrameInterval": "Frame interval"
    }

    info = ""
    for key, label in label_names.items():
        if key in video_info:
            info += f"{label}: {video_info[key]}\n"
    return info

def update_video_info_advanced_input(frame_interval, downscale_samples, downscale_target, video_info):
    """
    Save the frame interval to the current job state and update the markdown text above the frame interval slider
    """
    if video_info is None:
        return "Upload a video first.", video_info
        
    video_info['DownScaleSamples'] = downscale_samples
    video_info['FrameInterval'] = frame_interval
    try:
        if downscale_samples:
            max_side = int(downscale_target[:-2]) # Remove the "px" from the "512px" format that the dropdown returns
            video_info['DownscaledWidth'], video_info['DownscaledHeight'] = af.downscale_dimensions(video_info['Width'], video_info['Height'], max_side) 
        else:
            video_info['DownscaledWidth'] = video_info['Width']
            video_info['DownscaledHeight'] = video_info['Height']

        frame_count = video_info['FrameCount']
        frame_rate = video_info['FrameRate']

        if not frame_count or not frame_rate:
            return "Video information not available.", video_info
            
        samples_count = (frame_count // frame_interval) + 2
        samples_per_second = frame_rate / frame_interval
        cost = af.calculate_video_input_cost(video_info['DownscaledWidth'], video_info['DownscaledHeight'], samples_count)
        return f"Minimum input cost: {cost}<br />Video will be split into {samples_count} samples total. Or approximately {samples_per_second:.1f} samples per second.", video_info
    except Exception as e:
        return f"Error calculating frame interval: {str(e)}", video_info

# --- Tab 1 UI State Management ---
def trigger_frame_interval_slider_rerender(on_video_uploaded_state):
    return not on_video_uploaded_state

def get_slider_config(video_info):
    if not video_info:
        return None
        
    total_frames = video_info.get('FrameCount', {}).get('Value', 0)
    framerate = video_info.get('FrameRate', {}).get('Value', 0)
    
    if not total_frames or not framerate:
        return None
        
    max_interval = total_frames // 2
    
    return {
        'minimum': 1,
        'maximum': max_interval,
        'step': 1,
        'value': framerate,
        'label': f"Frame Interval (1-{max_interval})"
    }

def get_generate_descriptions_button(is_interactive):
    return gr.Button("Generate Video Description and Audio Sources", interactive=is_interactive)

def get_generate_audio_button(is_interactive):
    return gr.Button("Generate Audio", variant="primary", interactive=is_interactive)

def set_generate_buttons_active():
    return get_generate_descriptions_button(True), get_generate_audio_button(True)

def set_generate_buttons_inactive():
    return get_generate_descriptions_button(False), get_generate_audio_button(False)

def go_to_tab(id):
    return gr.Tabs(selected=id)

# --- Tab 1 Functionality ---
def on_video_upload(video):
    if video is None:
        return get_generate_descriptions_button(False), get_generate_audio_button(False), None, "", None, ""
    try:
        video_info = af.get_video_info(video)
    except Exception as e:
        gr.Warning(f"Error: {e}")
    return get_generate_descriptions_button(True), get_generate_audio_button(True), video_info, "", None, ""

def generate_descriptions(video, video_info, prompt_instruction, vision_lm_api_key):
    if not video or video_info is None:
        return None, "", video_info
    try:
        audio_sources, _ = af.process_video(video, video_info['FrameInterval'], video_info['DownscaledWidth'], video_info['DownscaledHeight'], prompt_instruction, vision_lm_api_key)
        json_output = json.dumps(audio_sources, indent=4)
        return json_output, json_output, audio_sources
    except Exception as e:
        gr.Warning(f"Error: {e}")
        return None, "", {}

def generate_all_audio(video, video_info, prompt_instruction, generate_descriptions_json_output, generate_descriptions_json_textbox, vision_lm_api_key, ttsfx_api_key, progress=gr.Progress()):
    # Check if user has provided their own descriptions through the advanced input textbox
    valid_json = True
    if generate_descriptions_json_textbox and not generate_descriptions_json_textbox.isspace():
        # Validate the expected structure
        try:
            audio_sources = json.loads(generate_descriptions_json_textbox)
            if audio_sources and not isinstance(audio_sources, dict):
                valid_json = False
            else:
                required_keys = {'AudioSources', 'AmbientAudioSources'}
                if not all(key in audio_sources for key in required_keys):
                    valid_json = False 
                if not isinstance(audio_sources['AudioSources'], list) or not isinstance(audio_sources['AmbientAudioSources'], list):
                    valid_json = False
        except:
            valid_json = False
    else:
        valid_json = False
    # If descriptions weren't given, generate them now
    if not valid_json:
        progress((1, 3), desc="Processing video")
        try:
            audio_sources, _ = af.process_video(video, video_info['FrameInterval'], video_info['DownscaledWidth'], video_info['DownscaledHeight'], prompt_instruction, vision_lm_api_key)
            json_output = json.dumps(audio_sources, indent=4)
            generate_descriptions_json_output = json_output
            generate_descriptions_json_textbox = json_output
        except Exception as e:
            raise gr.Error(f"Could not generate audio: {e}")
    # Generate audio files all the audio sources 
    progress((2, 3), desc="Generating audio")
    try:
        audio_sources = af.generate_all_audio(audio_sources, ttsfx_api_key)
    except Exception as e:
        raise gr.Error(f"Could not generate audio: {e}")
    return "", audio_sources, generate_descriptions_json_output, generate_descriptions_json_textbox

# --- Tab 2 UI State Management ---
def copy_video_info_to_edit_tab(video_path, video_info):
    video_info['VideoPath'] = video_path
    return video_info

def copy_video_info_to_edit_tab_if_none(video_path, video_input_info, video_edit_info):
    if not video_edit_info and video_input_info:
        video_edit_info = copy_video_info_to_edit_tab(video_path, video_input_info)
    return video_edit_info

def set_render_button_state(unrendered_changes_flag):
    return gr.Button("Combine All Audio & Render Video", variant="primary", interactive=unrendered_changes_flag)

def reset_new_audio_source_counter():
    return 0

def set_buttons_state_selected_audio_source(selected_audio_source):
    set_interactive = selected_audio_source is not None
    return gr.Button(value="Delete Selected Audio Source", variant="stop", interactive=set_interactive), gr.Button("Generate", variant="primary", interactive=set_interactive), gr.Button("Save Changes", interactive=set_interactive)

def sync_form_to_selected_audio_source(selected_audio_source):
    accordion_label = "Edit Audio Source Properties"
    if selected_audio_source is None:
        return gr.Accordion(label=accordion_label, open=False), 1.0, None, ""
    return gr.Accordion(label=accordion_label, open=True), selected_audio_source.get('Volume', 1.0), selected_audio_source.get('AudioPath', None), selected_audio_source['SoundDescription']

# --- Tab 2 VisTimelineData & AudioSource helper functions  ---
def parse_single_audio_source(audio_source, video_fps, group_id):
    timeline_item = {
        "id": audio_source['SourceSlugID'],
        "content": audio_source['SoundDescription'],
        "group": group_id,
        "start": parse_frame_to_timestamp(audio_source['StartFrameIndex'], video_fps),
        "end": parse_frame_to_timestamp(audio_source['EndFrameIndex'], video_fps)
    }
    return timeline_item

def parse_audio_sources_to_timeline_data(audio_sources, video_info):
    video_fps = video_info['FrameRate']
    last_frame = video_info['FrameCount'] - 1
    timeline_data = {
        "groups": [{"id": "track-length", "content": ""}, {"id": 1, "content": ""}, {"id": 2, "content": ""}],
        "items": [
            {
                "id": TRACK_LENGTH_ID,
                "content": "", 
                "group": "track-length", 
                "selectable": False, 
                "type": "background", 
                "start": 0, 
                "end": parse_frame_to_timestamp(last_frame, video_fps),
                "className": "color-primary-600"
            }
        ]
    }
    for audio_source in audio_sources.get('AudioSources', []):
        timeline_data['items'].append(parse_single_audio_source(audio_source, video_fps, 1))
    for ambient_audio_source in audio_sources.get('AmbientAudioSources', []):
        timeline_data['items'].append(parse_single_audio_source(ambient_audio_source, video_fps, 2))
    return timeline_data

def get_audio_source_by_slug(audio_sources, slug):
    for audio_source in audio_sources.get('AudioSources', []):
        if audio_source['SourceSlugID'] == slug:
            return audio_source
    for audio_source in audio_sources.get('AmbientAudioSources', []):
        if audio_source['SourceSlugID'] == slug:
            return audio_source
    return None

def update_audio_source_with_timeline_item_data(audio_source, timeline_item, max_duration, frame_rate):
    start_ms = max(0, parse_date_to_milliseconds(timeline_item["start"]))
    end_ms = min(max_duration, parse_date_to_milliseconds(timeline_item["end"]))
    audio_source['StartFrameIndex'] = int((start_ms / 1000) * frame_rate)
    audio_source['EndFrameIndex'] = int((end_ms / 1000) * frame_rate)
    audio_source['Duration'] = (end_ms - start_ms) / 1000
    return audio_source

# --- Tab 2 Timeline  ---
def focus_timeline_on_tab_select(set_timeline_window_on_next_tab_change, trigger_timeline_window_focus):
    if set_timeline_window_on_next_tab_change == True:
        return False, not trigger_timeline_window_focus
    return set_timeline_window_on_next_tab_change, trigger_timeline_window_focus

def focus_timeline_on_new_source_added(audio_sources, trigger_timeline_window_focus):
    # Only focus the timeline if there's only one audio source because this would mean there were none earlier
    audio_count = len(audio_sources.get('AudioSources', []) + audio_sources.get('AmbientAudioSources', []))
    if audio_count > 1:
        return trigger_timeline_window_focus
    return not trigger_timeline_window_focus 

def on_timeline_item_select(audio_sources, event_data: gr.EventData):
    selected_ids = event_data._data
    if not selected_ids:
        return None
    return get_audio_source_by_slug(audio_sources, selected_ids[0]) # Because we instantiate all timeline items with their ids set to the audio_source's slug

def on_timeline_input(timeline: dict[str, any], all_audio_sources, video_info):
    if hasattr(timeline, "model_dump"):
        data = timeline.model_dump(exclude_none=True)
    else:
        data = timeline

    video_duration_ms = video_info['Duration'] * 1000
    frame_rate = video_info['FrameRate']

    for audio_source in all_audio_sources.get('AudioSources', []):
        for timeline_item in data['items']:
            if timeline_item['id'] == audio_source['SourceSlugID']:
                audio_source = update_audio_source_with_timeline_item_data(audio_source, timeline_item, video_duration_ms, frame_rate)
                break
    for ambient_audio_source in all_audio_sources.get('AmbientAudioSources', []):
        for timeline_item in data['items']:
            if timeline_item['id'] == ambient_audio_source['SourceSlugID']:
                ambient_audio_source = update_audio_source_with_timeline_item_data(ambient_audio_source, timeline_item, video_duration_ms, frame_rate)
                break
    return all_audio_sources

# --- Tab 2 Functionality ---
def comp_all_audio_to_video(audio_sources, video_info):
    try:
        input_video_path = video_info['VideoPath']
        if not audio_sources:
            input_video_path
            return 
    
        output_directory = "output_videos"
        # Ensure the output directory exists
        if not os.path.exists(output_directory):
            os.makedirs(output_directory)
        else:
            # Clear the output directory before saving the new video
            for filename in os.listdir(output_directory):
                file_path = os.path.join(output_directory, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    raise OSError(f"Failed to delete {file_path}. Reason: {e}")

        # Generate a unique output filename
        input_filename = os.path.basename(input_video_path)
        file_name_without_extension, file_extension = os.path.splitext(input_filename)
        output_video_name = f"{file_name_without_extension}_output{file_extension}"
        output_video_path = os.path.join(output_directory, output_video_name)
        output_video_path = af.combine_video_and_audio(audio_sources, input_video_path, output_video_path)
    except Exception as e:
        gr.Warning(f"Failed to add the audio to the video: {e}")
    return output_video_path

def generate_new_audio(prompt, audio_player, selected_audio_source, ttsfx_api_key):
    if not selected_audio_source:
        return audio_player
    try:
        new_audio_file_path = af.generate_audio(prompt, selected_audio_source.get("Duration"), ttsfx_api_key)
        if new_audio_file_path:
            return new_audio_file_path
        return audio_player
    except:
        return audio_player
    
def add_new_audio_source(all_audio_sources, new_audio_sources_counter):
    new_audio_sources_counter += 1
    new_audio_source = {
        'SourceSlugID': f"NewAudioSource{new_audio_sources_counter}",
        'StartFrameIndex': 0,
        'EndFrameIndex': 75,
        'Duration': 3.0,
        'AudioPath': None,
        'SoundDescription': f"New audio source {new_audio_sources_counter}",
        'Volume': 1.0
    }
    audio_sources = all_audio_sources.get('AudioSources', [])
    audio_sources.append(new_audio_source)
    return {"AudioSources": audio_sources, "AmbientAudioSources": all_audio_sources.get('AmbientAudioSources', [])}, new_audio_sources_counter

def delete_selected_audio_source(selected_audio_source, all_audio_sources):
    if not selected_audio_source:
        return None, all_audio_sources
    
    slug_to_delete = selected_audio_source['SourceSlugID']
    audio_sources = all_audio_sources.get('AudioSources', [])
    audio_sources = [source for source in audio_sources if source['SourceSlugID'] != slug_to_delete]

    ambient_audio_sources = all_audio_sources.get('AmbientAudioSources', [])
    ambient_audio_sources = [source for source in ambient_audio_sources if source['SourceSlugID'] != slug_to_delete]

    return None, {'AudioSources': audio_sources, 'AmbientAudioSources': ambient_audio_sources}

def overwrite_changes_to_selected_audio_source(volume, audio_path, prompt, selected_audio_source, all_audio_sources):
    if not selected_audio_source:
        return all_audio_sources, selected_audio_source
    
    selected_slug = selected_audio_source['SourceSlugID']
    updated_source = get_audio_source_by_slug(all_audio_sources, selected_slug)
    updated_source.update({
        'SoundDescription': prompt,
        'AudioPath': audio_path,
        'Volume': float(volume)
    })
    
    audio_sources = all_audio_sources.get('AudioSources', [])
    ambient_sources = all_audio_sources.get('AmbientAudioSources', [])

    for i, source in enumerate(audio_sources):
        if source['SourceSlugID'] == selected_slug:
            audio_sources[i] = updated_source
            return {'AudioSources': audio_sources, 'AmbientAudioSources': ambient_sources}, selected_audio_source # Return early to skip the second loop
    for i, source in enumerate(ambient_sources):
        if source['SourceSlugID'] == selected_slug:
            ambient_sources[i] = updated_source
            break
    return {'AudioSources': audio_sources, 'AmbientAudioSources': ambient_sources}, selected_audio_source

# --- Custom JS and CSS ---
current_dir = os.path.dirname(os.path.abspath(__file__))
js_path = os.path.join(current_dir, 'custom_script.js')
css_path = os.path.join(current_dir, 'custom_style.css')

with open(js_path, 'r') as f:
    js_content = f.read()

with open(css_path, 'r') as f:
    css_content = f.read()

head = f"""<script>{js_content}</script><style>{css_content}.vis-custom-time.{TIMELINE_ID} {{pointer-events: none !important;}}</style>"""

# --- Gradio UI ---
with gr.Blocks(head=head) as ui:
    # Initialize per-user Gradio states with default values
    ttsfx_api_key_state = gr.State(value=None)
    vision_lm_api_key_state = gr.State(value=None)

    video_input_info_state = gr.State(value={})
    video_edit_info_state = gr.State(value={})

    audio_sources_state = gr.State(value={})
    selected_audio_source_state = gr.State(value={})
    new_audio_sources_counter = gr.State(value=0)

    trigger_frame_interval_slider_render = gr.State(value=False)
    trigger_timeline_window_focus = gr.State(value=False)
    set_timeline_window_on_next_tab_change = gr.State(value=True)
    unrendered_changes_flag = gr.State(value=False)

    gr.Markdown("### Auto-Foley Editor")

    with gr.Tabs() as tabs:
        # --- Tab 1 ---
        with gr.TabItem("Input", id=0) as input_tab:
            with gr.Row(equal_height=True):
                video_input = gr.Video(label="Upload a Video", height=206, sources='upload')
                video_info_display = gr.Textbox(label="Video Information", lines=6, interactive=False)

            with gr.Accordion("Input control", open=False):
                @gr.render(inputs=[video_input_info_state], triggers=[trigger_frame_interval_slider_render.change])
                def render_frame_interval_slider(video_info):
                    total_frames = video_info.get('FrameCount', 0)
                    frame_rate = video_info.get('FrameRate', 0)
                    max_interval = total_frames // 2

                    with gr.Row(equal_height=True):
                        with gr.Column():
                            cost_and_frame_interval_info = gr.Markdown("Upload a video")
                            frame_interval_slider = gr.Slider(
                                elem_id="frame_interval_slider",
                                minimum=1,
                                maximum=max_interval,
                                step=1,
                                value=frame_rate,
                                label=f"Frame Interval ({max_interval}-1)"
                            )

                        with gr.Column(scale=0):
                            downscale_samples_checkbox = gr.Checkbox(
                                value=True,
                                interactive=True,
                                label="Downscale samples"
                            )

                            downscale_resolution_dropdown = gr.Dropdown(
                                choices=["512px", "768px", "1024px"], 
                                value="512px",
                                type="value",
                                interactive=True,
                                label="Max side"
                            )

                    frame_interval_slider.change(
                        fn=update_video_info_advanced_input,
                        inputs=[frame_interval_slider, downscale_samples_checkbox, downscale_resolution_dropdown, video_input_info_state],
                        outputs=[cost_and_frame_interval_info, video_input_info_state]
                    )

                    downscale_samples_checkbox.change(
                        fn=update_video_info_advanced_input,
                        inputs=[frame_interval_slider, downscale_samples_checkbox, downscale_resolution_dropdown, video_input_info_state],
                        outputs=[cost_and_frame_interval_info, video_input_info_state]
                    )

                    downscale_resolution_dropdown.change(
                        fn=update_video_info_advanced_input,
                        inputs=[frame_interval_slider, downscale_samples_checkbox, downscale_resolution_dropdown, video_input_info_state],
                        outputs=[cost_and_frame_interval_info, video_input_info_state]
                    )

                custom_instruction_textbox = gr.Textbox(label="Optional custom instruction for the LLM:", interactive=True)

                with gr.Accordion("Observe or edit the LLM's response before generating audio with it:", open=False):
                    generate_descriptions_button = get_generate_descriptions_button(False)
                    with gr.Tabs():
                        with gr.Tab("View"):
                            generate_descriptions_json_output = gr.JSON(label="JSON")
                        with gr.Tab("Edit"):
                            generate_descriptions_json_textbox = gr.Textbox(label="JSON", lines=22, interactive=True)
                
            generate_all_audio_button = get_generate_audio_button(False)
            generate_all_progress_textbox = gr.Textbox(show_label=False, visible=False)

        # --- Tab 2 ---
        with gr.TabItem("Output & Edit", id=1) as output_tab:
            video_comp_output = gr.Video(label="Result", height=480, interactive=False, elem_id=OUTPUT_VIDEO_ID)

            with gr.Row():
                with gr.Column():
                    with gr.Row():
                        add_audio_source_button = gr.Button("Add New Audio Source")
                        delete_audio_source_button = gr.Button(value="Delete Selected Audio Source", variant="stop", interactive=False)
                with gr.Column():
                    comp_audio_button = gr.Button("Combine All Audio & Render Video", variant="primary", interactive=False)

            timeline = VisTimeline(
                value={"groups": [{"id": "track-length", "content": ""}, {"id": 1, "content": ""}, {"id": 2, "content": ""}], "items": []},
                options={
                    "moment": "+00:00",
                    "showCurrentTime": False,
                    "editable": {
                        "add": False,
                        "remove": False,
                        "updateGroup": False,
                        "updateTime": True
                    },
                    "itemsAlwaysDraggable": {
                        "item": True,
                        "range": True
                    },
                    "showMajorLabels": False,
                    "format": {
                        "minorLabels": {
                            "millisecond": "mm:ss.SSS",
                            "second": "mm:ss",
                            "minute": "mm:ss",
                            "hour": "HH:mm:ss"
                        }
                    },
                    "start": 0,
                    "end": 10000,
                    "min": 0,
                    "max": 22000,
                    "zoomMin": 1000,
                },
                elem_id=TIMELINE_ID
            )

            with gr.Accordion("Edit Audio Source Properties", open=False) as selected_source_accordion:
                with gr.Group():
                    selected_audio_volume_slider = gr.Slider(label="Volume", minimum=0.0, maximum=2.0, step=0.01, value=1.0)
                    selected_audio_player = gr.Audio(label="Audio", type="filepath")
                with gr.Accordion("Generate New Audio", open=False):
                    selected_audio_prompt_textbox = gr.Textbox(label="Prompt")
                    selected_audio_overwrite_audio_button = gr.Button("Generate", variant="primary", interactive=False)
                save_changes_button = gr.Button("Save Changes", interactive=False)

        # --- Tab 3 ---
        with gr.TabItem("Set API Keys", id=2) as settings_tab:
            vision_lm_api_key_textbox = gr.Textbox(label="OpenAI API Key", type='password')
            ttsfx_api_key_textbox = gr.Textbox(label="ElevenLabs API Key", type='password')

    # Tab 1 interactions
    input_tab.select(
        fn=lambda: False, outputs=set_timeline_window_on_next_tab_change
    )

    video_input.change(
        fn=on_video_upload,
        inputs=video_input,
        outputs=[generate_descriptions_button, generate_all_audio_button, video_input_info_state, custom_instruction_textbox, generate_descriptions_json_output, generate_descriptions_json_textbox]
    ).then(
        fn=trigger_frame_interval_slider_rerender,
        inputs=trigger_frame_interval_slider_render,
        outputs=trigger_frame_interval_slider_render
    )

    video_input_info_state.change(
        fn=format_video_info,
        inputs=video_input_info_state,
        outputs=video_info_display
    )

    generate_descriptions_button.click(
        fn=set_generate_buttons_inactive, outputs=[generate_descriptions_button, generate_all_audio_button]
    ).then(
        fn=generate_descriptions,
        inputs=[video_input, video_input_info_state, custom_instruction_textbox, vision_lm_api_key_state],
        outputs=[generate_descriptions_json_output, generate_descriptions_json_textbox, audio_sources_state],
        concurrency_id="long_job"
    ).then(
        fn=set_generate_buttons_active, outputs=[generate_descriptions_button, generate_all_audio_button]
    )

    generate_all_audio_button.click(fn=lambda: 0, outputs=new_audio_sources_counter)

    generate_all_audio_button.click(
        fn=copy_video_info_to_edit_tab,
        inputs=[video_input, video_input_info_state],
        outputs=video_edit_info_state
    ).then(
        fn=lambda: 0, outputs=new_audio_sources_counter
    ).then(
        fn=lambda: True, outputs=set_timeline_window_on_next_tab_change
    ).then(
        fn=lambda: gr.Textbox(show_label=False, visible=True), outputs=generate_all_progress_textbox
    ).then(
        fn=set_generate_buttons_inactive, outputs=[generate_descriptions_button, generate_all_audio_button]
    ).then(
        fn=generate_all_audio,
        inputs=[video_input, video_edit_info_state, custom_instruction_textbox, generate_descriptions_json_output, generate_descriptions_json_textbox, vision_lm_api_key_state, ttsfx_api_key_state],
        outputs=[generate_all_progress_textbox, audio_sources_state, generate_descriptions_json_output, generate_descriptions_json_textbox],
        concurrency_id="long_job"
    ).then(
        fn=parse_audio_sources_to_timeline_data,
        inputs=[audio_sources_state, video_edit_info_state],
        outputs=timeline
    ).then(
        fn=lambda: go_to_tab(1),
        inputs=[],
        outputs=tabs
    ).then(
        fn=set_generate_buttons_active, outputs=[generate_descriptions_button, generate_all_audio_button]
    ).then(
        fn=lambda: gr.Textbox(show_label=False, visible=False), outputs=generate_all_progress_textbox
    ).then(
        fn=comp_all_audio_to_video,
        inputs=[audio_sources_state, video_edit_info_state],
        outputs=video_comp_output,
        concurrency_id="comp"
    ).then(
        fn=lambda: False, outputs=unrendered_changes_flag
    ).then(
        fn=None,
        js=f'() => initVideoSync("{OUTPUT_VIDEO_ID}", "{TIMELINE_ID}", "{TRACK_LENGTH_ID}")'
    )

    # Tab 2 interactions
    output_tab.select(
        fn=copy_video_info_to_edit_tab_if_none,
        inputs=[video_input, video_input_info_state, video_edit_info_state],
        outputs=[video_edit_info_state]
    ).then(
        fn=focus_timeline_on_tab_select,
        inputs=[set_timeline_window_on_next_tab_change, trigger_timeline_window_focus],
        outputs=[set_timeline_window_on_next_tab_change, trigger_timeline_window_focus]
    )

    unrendered_changes_flag.change(
        fn=set_render_button_state,
        inputs=unrendered_changes_flag,
        outputs=comp_audio_button
    )

    trigger_timeline_window_focus.change(
        fn=None,
        js=f'() => setTimelineWindowToItemLength("{TIMELINE_ID}", "{TRACK_LENGTH_ID}")'
    )

    comp_audio_button.click(
        fn=comp_all_audio_to_video,
        inputs=[audio_sources_state, video_edit_info_state],
        outputs=video_comp_output,
        concurrency_id="comp"
    ).then(
        fn=lambda: False, outputs=unrendered_changes_flag
    ).then(
        fn=None,
        js=f'() => initVideoSync("{OUTPUT_VIDEO_ID}", "{TIMELINE_ID}", "{TRACK_LENGTH_ID}")'
    )

    add_audio_source_button.click(
        fn=add_new_audio_source,
        inputs=[audio_sources_state, new_audio_sources_counter],
        outputs=[audio_sources_state, new_audio_sources_counter]
    ).then(
        fn=parse_audio_sources_to_timeline_data,
        inputs=[audio_sources_state, video_edit_info_state],
        outputs=timeline
    ).then(
        fn=focus_timeline_on_new_source_added,
        inputs=[audio_sources_state, trigger_timeline_window_focus],
        outputs=trigger_timeline_window_focus
    ).then(
        fn=lambda: True, outputs=unrendered_changes_flag
    )

    delete_audio_source_button.click(
        fn=delete_selected_audio_source,
        inputs=[selected_audio_source_state, audio_sources_state],
        outputs=[selected_audio_source_state, audio_sources_state]
    ).then(
        fn=parse_audio_sources_to_timeline_data,
        inputs=[audio_sources_state, video_edit_info_state],
        outputs=timeline
    ).then(
        fn=lambda: True, outputs=unrendered_changes_flag
    )

    timeline.item_select(
        fn=on_timeline_item_select, 
        inputs=[audio_sources_state], 
        outputs=selected_audio_source_state
    )

    timeline.input(
            fn=on_timeline_input, 
            inputs=[timeline, audio_sources_state, video_edit_info_state], 
            outputs=audio_sources_state
    ).then(
        fn=lambda: True, outputs=unrendered_changes_flag
    )

    selected_audio_source_state.change(
        fn=sync_form_to_selected_audio_source,
        inputs=selected_audio_source_state,
        outputs=[selected_source_accordion, selected_audio_volume_slider, selected_audio_player, selected_audio_prompt_textbox]
    ).then(
        fn=set_buttons_state_selected_audio_source,
        inputs=selected_audio_source_state,
        outputs=[delete_audio_source_button, selected_audio_overwrite_audio_button, save_changes_button]
    )

    selected_audio_overwrite_audio_button.click(
        fn=generate_new_audio,
        inputs=[selected_audio_prompt_textbox, selected_audio_player, selected_audio_source_state, ttsfx_api_key_state],
        outputs=selected_audio_player
    )

    save_changes_button.click(
        fn=overwrite_changes_to_selected_audio_source,
        inputs=[
            selected_audio_volume_slider, 
            selected_audio_player, 
            selected_audio_prompt_textbox, 
            selected_audio_source_state, 
            audio_sources_state
        ],
        outputs=[audio_sources_state, selected_audio_source_state]
    ).then(
        fn=parse_audio_sources_to_timeline_data,
        inputs=[audio_sources_state, video_edit_info_state],
        outputs=timeline
    ).then(
        fn=lambda: True, outputs=unrendered_changes_flag
    )

    # Tab 3 interactions
    vision_lm_api_key_textbox.input(
        fn=lambda a: a,
        inputs=vision_lm_api_key_textbox,
        outputs=vision_lm_api_key_state
    )

    ttsfx_api_key_textbox.input(
        fn=lambda a: a,
        inputs=ttsfx_api_key_textbox,
        outputs=ttsfx_api_key_state
    )

    ui.load(
        fn=lambda: (os.getenv('AUTO_FOLEY_DEFAULT_VISION_LM_API_KEY'), os.getenv('AUTO_FOLEY_DEFAULT_TTSFX_API_KEY')),
        outputs=[vision_lm_api_key_state, ttsfx_api_key_state]
    )

if __name__ == "__main__":
    ui.launch(show_api=False)
