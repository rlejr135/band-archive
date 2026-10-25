import { useContext } from 'react';
import { UploadContext } from '../../context/UploadContext.jsx';
import FileUpload from '../common/FileUpload.jsx';

// The application shell supplies the upload context. Keep embedded/read-only
// workspace surfaces inert when no upload provider is present.
export const BandUploadInput = (props) => useContext(UploadContext) ? <FileUpload {...props} /> : null;
