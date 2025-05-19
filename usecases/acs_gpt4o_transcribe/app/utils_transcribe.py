import pyaudio

def list_audio_input_devices() -> None:
    """
    Print all available input devices (microphones) for user selection.
    """
    p = pyaudio.PyAudio()
    print("\nAvailable audio input devices:")
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        if dev["maxInputChannels"] > 0:
            print(f"{i}: {dev['name']}")
    p.terminate()


def choose_default_audio_device() -> int:
    """
    Return the index of the default audio input device, or prompt user if >1.
    """
    p = pyaudio.PyAudio()
    mic_indices = [
        i
        for i in range(p.get_device_count())
        if p.get_device_info_by_index(i)["maxInputChannels"] > 0
    ]
    p.terminate()
    if len(mic_indices) == 0:
        raise RuntimeError("No microphone devices found.")
    if len(mic_indices) == 1:
        print(f"Auto-selecting only available input device: {mic_indices[0]}")
        return mic_indices[0]
    list_audio_input_devices()
    try:
        idx = int(
            input(f"Select audio input device index [{mic_indices[0]}]: ")
            or mic_indices[0]
        )
    except Exception:
        idx = mic_indices[0]
    return idx