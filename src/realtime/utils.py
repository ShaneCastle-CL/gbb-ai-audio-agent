"""
Utility functions for audio and base64 conversion.
"""

import numpy as np
import base64
import logging
from typing import Union

logger = logging.getLogger(__name__)


def float_to_16bit_pcm(float32_array: np.ndarray) -> np.ndarray:
    """
    Convert a float32 numpy array to int16 PCM format.

    Args:
        float32_array (np.ndarray): Input array of dtype float32.

    Returns:
        np.ndarray: Output array of dtype int16.
    """
    if float32_array.dtype != np.float32:
        logger.warning("Input array is not float32, attempting conversion.")
        float32_array = float32_array.astype(np.float32)

    int16_array = np.clip(float32_array, -1, 1) * 32767
    return int16_array.astype(np.int16)


def base64_to_array_buffer(base64_string: str) -> np.ndarray:
    """
    Decode a base64 string into a numpy uint8 array buffer.

    Args:
        base64_string (str): Base64-encoded input string.

    Returns:
        np.ndarray: Decoded buffer as uint8 numpy array.
    """
    try:
        binary_data = base64.b64decode(base64_string)
        return np.frombuffer(binary_data, dtype=np.uint8)
    except Exception as e:
        logger.error(f"Failed to decode base64 string: {e}")
        raise


def array_buffer_to_base64(array_buffer: np.ndarray) -> str:
    """
    Encode a numpy array buffer into a base64 string.

    Args:
        array_buffer (np.ndarray): Input array buffer.

    Returns:
        str: Base64-encoded string.
    """
    try:
        if array_buffer.dtype == np.float32:
            logger.debug("Converting float32 array to int16 PCM before encoding.")
            array_buffer = float_to_16bit_pcm(array_buffer)
        array_bytes = array_buffer.tobytes()
        return base64.b64encode(array_bytes).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to encode array buffer: {e}")
        raise


def merge_int16_arrays(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """
    Merge two int16 numpy arrays into a single array.

    Args:
        left (np.ndarray): First array (must be int16).
        right (np.ndarray): Second array (must be int16).

    Returns:
        np.ndarray: Concatenated int16 array.

    Raises:
        ValueError: If input arrays are not both int16.
    """
    if left.dtype != np.int16 or right.dtype != np.int16:
        logger.error("Attempted to merge arrays that are not int16.")
        raise ValueError("Both arrays must have dtype int16.")
    return np.concatenate((left, right))
