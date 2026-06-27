import os
import sys

# Add the current directory to sys.path so 'modules' can be imported
sys.path.insert(0, os.path.abspath('.'))

from modules.screen_reader import read_active_screen

if __name__ == "__main__":
    print("Testing read_active_screen()...")
    result = read_active_screen()
    print("\n--- Result ---\n")
    print(result)
    print("\n--------------\n")
