import speech_recognition as sr

print("\n--- DETECTED AUDIO HARDWARE ---")
for index, name in enumerate(sr.Microphone.list_microphone_names()):
    print(f"Index {index}: {name}")
print("-------------------------------\n")