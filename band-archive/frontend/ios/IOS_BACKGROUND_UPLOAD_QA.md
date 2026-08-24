# iOS background upload device checklist

- On macOS, run `xcodebuild -project App/App.xcodeproj -scheme App -destination 'platform=iOS Simulator,name=iPhone 16' test`; confirm signing on a physical iPhone.
- Test PHPicker with local and iCloud-only videos, then lock the device, switch apps, terminate by the system, and separately force-quit. Force-quit cancels iOS background transfers; this is expected and the UI must say so.
- Exercise Wi-Fi to cellular changes, expired part URLs, cancel/retry, a near-1 GiB file, and notification denial.
- Cover both media (`song_id` plus optional `rehearsal_id`) and personal-log (`member_id`) uploads. Verify resume, ACK recovery, completion polling, and M4A-ready playback.
