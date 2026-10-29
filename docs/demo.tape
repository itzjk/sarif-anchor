Output docs/demo.gif
Require python3
Require git
Require sarif-anchor
Set Shell bash
Set FontSize 15
Set Width 1280
Set Height 700
Set Padding 18
Set Theme "Dracula"
Set TypingSpeed 18ms
Set PlaybackSpeed 1
Set WaitTimeout 15s
Env PS1 "$ "
Hide
Type `demo_repo="$(mktemp -d)/repo"`
Enter
Type `python3 examples/make_demo.py "$demo_repo"`
Enter
Wait+Screen /Created two commits/
Type `clear`
Enter
Sleep 500ms
Type `git -C "$demo_repo" status --short`
Enter
Wait+Screen / M example.py/
Type `sarif-anchor examples/report.sarif --repo "$demo_repo" --ref original`
Enter
Wait+Screen /discrepancies: false; exit 0/
Sleep 500ms
Show
Sleep 5s
Type `sarif-anchor examples/report.sarif --repo "$demo_repo" --ref shifted`
Enter
Wait+Screen /discrepancies: true; exit 1/
Sleep 7s
