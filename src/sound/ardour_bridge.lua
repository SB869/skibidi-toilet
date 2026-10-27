ardour {
	["type"]    = "EditorHook",
	name        = "sound bridge",
	author      = "sound",
	description = "Runs requests from the sound tools (import audio, run Lua) that they drop in a folder. Installed by `sound daw`; see src/sound/bridge.py.",
}

-- every 100 ms
function signals ()
	return LuaSignal.Set():add ({[LuaSignal.LuaTimerDS] = true})
end

function factory ()
	local dir = @BRIDGE_DIR@  -- filled in when installed
	local request, response = dir .. "/request", dir .. "/response"

	local function json (v)
		local t = type (v)
		if t == "nil" then return "null"
		elseif t == "boolean" then return tostring (v)
		elseif t == "number" then
			if v ~= v or v == math.huge or v == -math.huge then return "null" end
			if math.type (v) == "integer" then return string.format ("%d", v) end
			return string.format ("%.17g", v)
		elseif t == "string" then
			local escaped = v:gsub ('[%c"\\]', function (c)
				local named = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
				return named[c] or string.format ("\\u%04x", c:byte ())
			end)
			return '"' .. escaped .. '"'
		elseif t == "table" then
			local parts = {}
			if next (v) == nil or #v > 0 then
				for _, x in ipairs (v) do parts[#parts + 1] = json (x) end
				return "[" .. table.concat (parts, ",") .. "]"
			end
			for k, x in pairs (v) do parts[#parts + 1] = json (tostring (k)) .. ":" .. json (x) end
			return "{" .. table.concat (parts, ",") .. "}"
		end
		return json (tostring (v))  -- Ardour objects and the like
	end

	local function route_id (r) return r:to_stateful ():id ():to_s () end

	local function routes ()
		local list = {}
		for r in Session:get_routes ():iter () do
			if not r:is_master () and not r:is_monitor () and not r:is_auditioner () then
				list[#list + 1] = { id = route_id (r), name = r:name () }
			end
		end
		return list
	end

	local function find_track (key)
		for r in Session:get_routes ():iter () do
			if (route_id (r) == tostring (key) or r:name () == key) and not r:to_track ():isnil () then
				return r:to_track ()
			end
		end
		error ("no track called or with the id " .. tostring (key))
	end

	local commands = {}

	function commands.ping ()
		return { session = Session:name (), path = Session:path (), sample_rate = Session:nominal_sample_rate (),
		         tracks = routes () }
	end

	-- files: paths; at: sample position; track: id or name of a track to import onto (else new
	-- tracks, one per file); name: rename the new track (single file only)
	function commands.import (req)
		local files = C.StringVector ()
		for _, path in ipairs (req.files) do files:push_back (path) end
		local before = {}
		for _, r in ipairs (routes ()) do before[r.id] = true end
		local mode, track = Editing.ImportAsTrack, ARDOUR.Track ()
		if req.track then mode, track = Editing.ImportToTrack, find_track (req.track) end
		local pos = Temporal.timepos_t (req.at or 0)
		Editor:do_import (files, Editing.ImportDistinctFiles, mode, ARDOUR.SrcQuality.SrcBest,
			ARDOUR.MidiTrackNameSource.SMFFileAndTrackName, ARDOUR.MidiTempoMapDisposition.SMFTempoIgnore,
			pos, ARDOUR.PluginInfo (), track, false)
		local added = {}
		for r in Session:get_routes ():iter () do
			if not before[route_id (r)] and not r:is_master () and not r:is_monitor () and not r:is_auditioner () then
				if req.name and #req.files == 1 then r:set_name (req.name) end
				added[#added + 1] = { id = route_id (r), name = r:name () }
			end
		end
		return { added = added, tracks = routes () }
	end

	-- code: Lua run with Ardour's API (Session, Editor, ARDOUR, ...); returns what it returns and
	-- what it prints
	function commands.lua (req)
		local printed = {}
		local env = setmetatable ({ print = function (...)
			local parts = {}
			for i = 1, select ("#", ...) do parts[#parts + 1] = tostring ((select (i, ...))) end
			printed[#printed + 1] = table.concat (parts, "\t")
		end }, { __index = _ENV })
		local chunk, err = load (req.code, "lua", "t", env)
		if not chunk then error (err, 0) end
		local results = table.pack (chunk ())
		return { returned = results.n > 1 and { table.unpack (results, 1, results.n) } or results[1], printed = printed }
	end

	local function answer (text)
		local id = text:match ('%["id"%]%s*=%s*"(%w+)"')
		local ok, result = pcall (function ()
			local chunk, err = load ("return " .. text, "request", "t", {})
			if not chunk then error ("unreadable request: " .. err, 0) end
			local req = chunk ()
			local command = commands[req.command]
			if not command then error ("no command " .. tostring (req.command), 0) end
			if not Session then error ("no session is open", 0) end
			return command (req)
		end)
		local reply = { id = id, ok = ok }
		if ok then reply.result = result else reply.error = tostring (result) end
		return json (reply)
	end

	return function (signal, ref, ...)
		local f = io.open (request, "r")
		if not f then return end
		local text = f:read ("a")
		f:close ()
		os.remove (request)
		local ok, reply = pcall (answer, text)
		if not ok then reply = '{"ok":false,"error":' .. json (tostring (reply)) .. '}' end
		local out = io.open (response .. ".tmp", "w")
		if out then
			out:write (reply)
			out:close ()
			os.rename (response .. ".tmp", response)
		end
	end
end
