export const ErrorState = ({ message, retry }) => <div className="workspace-error" role="alert"><p>{message}</p>{retry && <button className="secondary-btn" onClick={retry}>다시 시도</button>}</div>;
