"""Export fixed reaction types and browser operation metadata without opening a database."""

import json
from pathlib import Path

from app.modules.content.comment_likes_schema import CommentLikesContract


def main() -> None:
    schema = CommentLikesContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "0.1.0"
    schema["x-everunfold-visitor-header"] = "X-Everunfold-Visitor"
    schema["x-everunfold-operations"] = [
        {
            "name": "readCommentLikes",
            "path": "/v1/comments/likes",
            "method": "GET",
            "input": "query",
            "response": "states",
        },
        {
            "name": "setCommentLike",
            "path": "/v1/comments/{comment_id}/like",
            "method": "PUT",
            "input": "mutation",
            "response": "state",
        },
    ]
    target = Path(__file__).resolve().parents[1] / "contracts" / "comment-likes-v0.1.schema.json"
    target.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
