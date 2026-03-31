I'd like to have a way to create a song with all the information in one call, so we don't need to create patterns, set up sounds, select drums. I'd like to have some sort of format (json, yaml, or even custom) that makes
up a song. Important things: the format should implement all the features the mcp sequencer has, but it shoul also be easily exportable to the circuit tracks directly as a circuit tracks project, removing features that  
 are not supported. The workflow I imagine is following:

User: let's make a techno song, epic.

And then the ai agent is able to connect to the circuit tracks, prepares the song yaml/json format, pass it to the MCP via a call to `set_song` or similar. Afterwards, if the user is happy:

User: let's save it to the Circuit

And then the ai agent would store it to the circuit by calling somethign like: `send_song_to_circuit_as_project` or similar.

Feel free to ask me any questions to clarify any doubts you might have.
