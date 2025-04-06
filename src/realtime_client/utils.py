import numpy as np
import base64
import logging

logger = logging.getLogger(__name__)

def float_to_16bit_pcm(float32_array: np.ndarray) -> np.ndarray:
    """
    Converts a numpy array of float32 amplitude data to a numpy array in int16 format.

    Args:
        float32_array (np.ndarray): Input float32 numpy array.

    Returns:
        np.ndarray: Output int16 numpy array.
    """
    int16_array = np.clip(float32_array, -1, 1) * 32767
    return int16_array.astype(np.int16)

def base64_to_array_buffer(base64_string: str) -> np.ndarray:
    """
    Converts a base64 encoded string to a numpy array buffer.

    Args:
        base64_string (str): Base64 encoded string.

    Returns:
        np.ndarray: Decoded numpy array buffer (uint8 dtype).
    """
    binary_data = base64.b64decode(base64_string)
    return np.frombuffer(binary_data, dtype=np.uint8)

def array_buffer_to_base64(array_buffer: np.ndarray) -> str:
    """
    Converts a numpy array buffer to a base64 encoded string.

    Args:
        array_buffer (np.ndarray): Input numpy array.

    Returns:
        str: Base64 encoded string.
    """
    if array_buffer.dtype == np.float32:
        array_buffer = float_to_16bit_pcm(array_buffer)
    array_buffer_bytes = array_buffer.tobytes()
    return base64.b64encode(array_buffer_bytes).decode('utf-8')

def merge_int16_arrays(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """
    Merges two numpy int16 arrays into one.

    Args:
        left (np.ndarray): First int16 array.
        right (np.ndarray): Second int16 array.

    Returns:
        np.ndarray: Concatenated int16 array.

    Raises:
        ValueError: If inputs are not int16 numpy arrays.
    """
    if not (isinstance(left, np.ndarray) and left.dtype == np.int16 and 
            isinstance(right, np.ndarray) and right.dtype == np.int16):
        raise ValueError("Both items must be numpy arrays of int16")
    
    return np.concatenate((left, right))
