import { Link } from "react-router-dom";
import { Shell } from "../components/Shell";
import { Blocked } from "../components/RoleGate";

export function NotFound() {
  return (
    <Shell>
      <Blocked heading="없는 페이지입니다" body="주소를 다시 확인해 주세요.">
        <Link className="btn" to="/intro">소개로 가기</Link>
      </Blocked>
    </Shell>
  );
}
