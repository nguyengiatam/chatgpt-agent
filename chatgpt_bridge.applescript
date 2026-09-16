-- Fixed AppleScript bridge to Microsoft Edge.
-- Every dynamic value arrives through argv, so no string is ever interpolated
-- into this source. That is what keeps prompts containing quotes, backslashes
-- or newlines from corrupting the script.
--
--   osascript chatgpt_bridge.applescript list
--   osascript chatgpt_bridge.applescript open <url>
--   osascript chatgpt_bridge.applescript loading <window> <tab>
--   osascript chatgpt_bridge.applescript focus <window> <tab>
--   osascript chatgpt_bridge.applescript eval <javascript> <window> <tab>

on run argv
	-- NB: `mode` is a term in Edge's scripting dictionary, so a variable of
	-- that name would be resolved against the app instead (error -1728).
	set theMode to item 1 of argv
	-- Built outside the tell block on purpose: inside it, `tab` resolves to
	-- Edge's `tab` CLASS and concatenates the literal word "tab".
	set sep to character id 9
	set eol to character id 10

	tell application "Microsoft Edge"
		if theMode is "list" then
			set out to ""
			set w to 0
			repeat with win in windows
				set w to w + 1
				set t to 0
				repeat with tb in tabs of win
					set t to t + 1
					set out to out & (w as text) & sep & (t as text) & sep & (URL of tb) & eol
				end repeat
			end repeat
			return out

		else if theMode is "open" then
			if (count of windows) is 0 then make new window
			make new tab at end of tabs of window 1 with properties {URL:(item 2 of argv)}
			return "ok"

		else if theMode is "loading" then
			set w to (item 2 of argv) as integer
			set t to (item 3 of argv) as integer
			if loading of tab t of window w then
				return "loading"
			else
				return "done"
			end if

		else if theMode is "focus" then
			set w to (item 2 of argv) as integer
			set t to (item 3 of argv) as integer
			set active tab index of window w to t
			return "ok"

		else if theMode is "eval" then
			set w to (item 3 of argv) as integer
			set t to (item 4 of argv) as integer
			return (execute tab t of window w javascript (item 2 of argv))
		end if
	end tell

	error "unknown mode: " & theMode
end run
