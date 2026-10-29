Output docs/demo.gif
Set Shell bash
Set FontSize 15
Set Width 1280
Set Height 700
Set Padding 18
Set Theme "Dracula"
Set TypingSpeed 18ms
Set PlaybackSpeed 1
Env PS1 "$ "
Hide
Type `demo_repo="$(mktemp -d)/repo"`
Enter
Type `python3 examples/make_demo.py "$demo_repo"`
Enter
Sleep 500ms
Type `clear`
Enter
Sleep 500ms
Show
Type `git -C "$demo_repo" status --short`
Enter
Sleep 1s
Type `sarif-anchor examples/report.sarif --repo "$demo_repo" --ref original`
Enter
Sleep 4s
Type `sarif-anchor examples/report.sarif --repo "$demo_repo" --ref shifted`
Enter
Sleep 6s
