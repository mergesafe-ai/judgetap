# Renders docs/demo.gif with charmbracelet/vhs (see .github/workflows/demo.yml).
Output docs/demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1100
Set Height 420
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms

Type 'judgetap guard test "git push --force origin main"'
Enter
Sleep 2s
Type 'judgetap guard test "rm -rf ~/projects"'
Enter
Sleep 3s
