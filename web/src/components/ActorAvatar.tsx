import type { ActorIdentity } from "../types";

export function ActorAvatar({ actor, className = "" }: { actor: ActorIdentity; className?: string }) {
  return (
    <span
      className={`actor-avatar actor-avatar-${actor.type}${className ? ` ${className}` : ""}`}
      aria-hidden="true"
      title={actor.name}
    >
      {actor.avatarUrl ? <img className="actor-avatar-image" src={actor.avatarUrl} alt="" referrerPolicy="no-referrer" /> : actor.type === "agent" ? "✦" : actor.name.slice(0, 1)}
    </span>
  );
}
