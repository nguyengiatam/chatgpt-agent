-- Fixed AppleScript bridge to Microsoft Edge.
-- Every dynamic value arrives through argv, so no string is ever interpolated
-- into this source. That is what keeps prompts containing quotes, backslashes
-- or newlines from corrupting the script.
--
--   osascript chatgpt_bridge.applescript list
--   osascript chatgpt_bridge.applescript open <url>
--   osascript chatgpt_bridge.applescript loading <tabid>
--   osascript chatgpt_bridge.applescript focus <tabid>
--   osascript chatgpt_bridge.applescript eval <javascript> <tabid>
--   osascript chatgpt_bridge.applescript close <tabid>

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
					set out to out & (w as text) & sep & (t as text) & sep & ((id of tb) as text) & sep & (URL of tb) & eol
				end repeat
			end repeat
			return out

		else if theMode is "open" or theMode is "open_window" then
			set nw to make new window
			set bounds of nw to {1440, 720, 1920, 1080}
			set URL of active tab of nw to (item 2 of argv)
			if theMode is "open" then return (id of active tab of nw) as integer as text
			return ((id of active tab of nw) as integer as text) & sep & ((id of nw) as integer as text)

		else if theMode is "loading" then
			set theId to (item 2 of argv) as integer
			repeat with win in windows
				repeat with tb in tabs of win
					if ((id of tb) as integer) is theId then
						if loading of tb then
							return "loading"
						else
							return "done"
						end if
					end if
				end repeat
			end repeat
			error "tab not found: " & (theId as text)

		else if theMode is "focus" then
			set theId to (item 2 of argv) as integer
			repeat with win in windows
				set t to 0
				repeat with tb in tabs of win
					set t to t + 1
					if ((id of tb) as integer) is theId then
						set active tab index of win to t
						return "ok"
					end if
				end repeat
			end repeat
			error "tab not found: " & (theId as text)

		else if theMode is "eval" then
			set theId to (item 3 of argv) as integer
			repeat with win in windows
				repeat with tb in tabs of win
					if ((id of tb) as integer) is theId then
						return (execute tb javascript (item 2 of argv))
					end if
				end repeat
			end repeat
			error "tab not found: " & (theId as text)

		else if theMode is "close" then
			set theId to (item 2 of argv) as integer
			repeat with win in windows
				repeat with tb in tabs of win
					if ((id of tb) as integer) is theId then
						close tb
						return "ok"
					end if
				end repeat
			end repeat
			error "tab not found: " & (theId as text)

		else if theMode is "close_window" then
			set winId to (item 2 of argv) as integer
			set tabId to (item 3 of argv) as integer
			set wantedUrl to item 4 of argv
			repeat with win in windows
				if ((id of win) as integer) is winId then
					if (count of tabs of win) is 1 then
						set tb to active tab of win
						if ((id of tb) as integer) is tabId and (URL of tb contains wantedUrl) then
							close win
							return "ok"
						end if
					end if
				end if
			end repeat
			return "no"
		end if
	end tell

	error "unknown mode: " & theMode
end run
