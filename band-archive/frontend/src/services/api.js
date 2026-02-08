const API_URL = 'http://localhost:5000';

export const fetchSongs = async () => {
  try {
    const response = await fetch(`${API_URL}/songs`);
    if (!response.ok) throw new Error('Failed to fetch songs');
    return await response.json();
  } catch (error) {
    console.error('API Error:', error);
    // Return dummy data if API fails for demo purposes
    return [
      { 
          id: 1, 
          title: 'Summer of 69', 
          artist: 'Bryan Adams', 
          genre: 'Rock', 
          difficulty: 2, 
          lyrics: 'I got my first real six-string...', 
          memo: 'Intro needs more power',
          media: [
              { type: 'audio', name: 'Practice-2024-02-01.mp3', url: '#' }, 
              { type: 'video', name: 'Live Gig.mp4', url: '#' }
          ]
      },
      { 
          id: 2, 
          title: 'Bohemian Rhapsody', 
          artist: 'Queen', 
          genre: 'Rock', 
          difficulty: 5, 
          lyrics: 'Is this the real life?...', 
          memo: 'Practice the opera section',
          media: []
      },
      { 
          id: 3, 
          title: 'Isn\'t She Lovely', 
          artist: 'Stevie Wonder', 
          genre: 'Pop/Soul', 
          difficulty: 3, 
          lyrics: 'Isn\'t she lovely...', 
          memo: 'Keyboard solo',
          media: [{ type: 'audio', name: 'Keys_solo_idea.wav', url: '#' }] 
      },
    ];
  }
};

export const createSong = async (songData) => {
  try {
    const response = await fetch(`${API_URL}/songs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(songData),
    });
    if (!response.ok) throw new Error('Failed to create song');
    return await response.json();
  } catch (error) {
    console.error('API Error:', error);
    // Mock successful creation for demo
    return { ...songData, id: Date.now() }; 
  }
};

export const updateSong = async (id, songData) => {
   // Placeholder for PUT
   console.log('Update song', id, songData);
   return { ...songData, id }; // Mock
};

export const deleteSong = async (id) => {
    // Placeholder for DELETE
    console.log('Delete song', id);
    return true; // Mock
};

export const uploadMedia = async (songId, file) => {
    // Placeholder for Upload
    console.log('Uploading media for song', songId, file.name);
    // Return a mock media object
    return {
        type: file.type.startsWith('image') ? 'image' : (file.type.startsWith('video') ? 'video' : 'audio'),
        name: file.name,
        url: URL.createObjectURL(file) // Create a local URL for preview
    };
};
