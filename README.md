## Track Depth Profiler ##

Blender add‑on for extracting depth profiles from footprint meshes

Location: View3D → N‑Panel → TrackProfiler (category “TrackProfiler”)
Built on Blender 5.0.1, but likely compatible with earlier versions.
Author: Peter Falkingham

### Usage ###
Select a mesh in Object mode – it must be a footprint or track mesh. Mesh should be aligned to world and positioned with the surrounding substrate surface at Z=0.

In the N‑panel, switch to the TrackProfiler tab.

Click Initialize Landmarks and, in the 3D‑View, click on the mesh surface in this order:

Hallux
MT1 head
MT5 head
Heel
• ESC or right‑click cancels the session (partial locators are removed).
• You can reposition the small spherical «locator» empties afterwards if needed.

When all four markers are present, the Analyse button becomes active.
Clicking it samples depth profiles (default 50 samples per segment) and stores the data.

#### View the graph ####

Use Show Graph / Hide Graph to toggle the overlay.
Drag the graph in the viewport or resize via its lower‑right grip.
Configure X‑axis layout (uniform/length‑proportional) and toggle individual tracks’ visibility/colours in the panel.
Export results

Click Export CSV to write all tracks to one file.
Metadata rows (landmark coords and segment lengths) precede the profile rows.
File defaults to footprint_profiles.csv in the current blend directory.
Clear results with the Clear Results button when finished; this does not remove your mesh or locators.

### Animated frame profiles ###

There is also a separate script, `trackprofiler_animated_profiles.py`, that can bake one profile per frame for an animated mesh without changing the addon itself.

How to use it:

1. Enable the TrackProfiler addon and make sure your mesh already has the four landmarks placed.
2. Open the script in Blender's Text Editor and run it.
3. Select the animated mesh you want to profile.
4. Click the new `Bake Animated Profiles` button in the TrackProfiler panel.
5. Scrub the timeline to see the baked lines in the graph. The current frame is shown in white, and the other frames fade from red at the start to blue at the end.

The script stores the baked data on the mesh object, so it can be restored after reopening the file.

### CSV format ###
mesh	segment	point_index	distance_along_transect_mm	depth_mm
segment values include the four transect labels (Hallux_MT1, etc.).
Metadata rows use META_LANDMARK_* and META_LENGTH_*.

## Limitations and Future Work ##
- Currently unable to save the analyses between sessions. Landmarks are kept though, so you can just select all the tracks and analyse again.