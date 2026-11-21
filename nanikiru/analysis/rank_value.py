"""Shared rank/points continuation model; no optimizer or training side effects."""

from dataclasses import dataclass, field, asdict
import json
import math
from pathlib import Path

from ..contracts import Metric
from ..rules.config import TENHOU_HANCHAN
from .rank_features import FEATURE_NAMES, FEATURE_VERSION, extract_features

MODEL_VERSION='shared-rank-linear-1'


@dataclass
class RankValueModel:
    # Rows 0..2 correspond to ranks 1..3; rank 4 has fixed zero logit.
    rank_weights: list[list[float]] = field(default_factory=lambda:[[0.0]*len(FEATURE_NAMES) for _ in range(3)])
    rank_bias: list[float] = field(default_factory=lambda:[0.0]*3)
    points_weights: list[float] = field(default_factory=lambda:[0.0]*len(FEATURE_NAMES))
    points_bias: float = 0.0
    trained: bool = False
    provenance: dict = field(default_factory=dict)

    def validate(self):
        if len(self.rank_weights)!=3 or len(self.rank_bias)!=3 or len(self.points_weights)!=len(FEATURE_NAMES):
            raise ValueError('Invalid coefficient dimensions')
        if any(len(w)!=len(FEATURE_NAMES) for w in self.rank_weights):raise ValueError('Invalid rank coefficient dimensions')
        numbers=[*self.rank_bias,self.points_bias,*self.points_weights,*(x for row in self.rank_weights for x in row)]
        if any(type(x) not in (int,float) or not math.isfinite(x) for x in numbers):raise ValueError('Coefficients must be finite')
        if type(self.trained) is not bool or not isinstance(self.provenance,dict):raise ValueError('Invalid training metadata')
        if self.trained and not self.provenance:raise ValueError('Trained model requires provenance')

    def forward(self, features):
        """Numerical primitive for future fitting/tests, NOT a calibrated prediction."""
        self.validate()
        if len(features)!=len(FEATURE_NAMES) or any(type(x) not in (int,float) or not math.isfinite(x) for x in features):
            raise ValueError('Invalid feature vector')
        logits=[b+sum(w*x for w,x in zip(row,features)) for row,b in zip(self.rank_weights,self.rank_bias)]+[0.0]
        points=self.points_bias+sum(w*x for w,x in zip(self.points_weights,features))
        if any(not math.isfinite(x) for x in [*logits,points]):raise ValueError('Numerical overflow')
        peak=max(logits);normalizer=peak+math.log(sum(math.exp(z-peak) for z in logits))
        logp=[z-normalizer for z in logits]
        return dict(probabilities=[math.exp(v) for v in logp],log_probabilities=logp,points=points)

    def evaluate(self, request):
        features=extract_features(request)
        if not self.trained:
            return {k:Metric('unavailable',details={'reason':'untrained_model','model':MODEL_VERSION})
                    for k in ('rank_probabilities','rank_value','result_points')}
        prediction=self.forward(features);p=prediction['probabilities']
        details={'model':MODEL_VERSION,'feature_version':FEATURE_VERSION,'scope':'continuation_under_training_policy_not_optimal_action_value'}
        return {k:Metric('conditional',v,'estimate',details.copy()) for k,v in (
            ('rank_probabilities',p),('rank_value',-sum((r+1)*v for r,v in enumerate(p))),('result_points',prediction['points']))}

    def loss(self, features, *, rank, result_points, l2=0.001):
        """Separate objectives; never add points loss to rank utility. No updates."""
        if type(rank) is not int or not 1<=rank<=4:raise ValueError('Rank must be 1..4')
        if type(result_points) not in (int,float) or not math.isfinite(result_points):raise ValueError('Invalid points label')
        if type(l2) not in (int,float) or not math.isfinite(l2) or l2<0:raise ValueError('Invalid L2')
        output=self.forward(features)
        return dict(rank_nll=-output['log_probabilities'][rank-1]+l2*sum(w*w for row in self.rank_weights for w in row),
                    points_mse=(output['points']-result_points)**2+l2*sum(w*w for w in self.points_weights))

    def save(self,path):
        self.validate()
        data=dict(model_version=MODEL_VERSION,feature_version=FEATURE_VERSION,feature_names=FEATURE_NAMES,
                  rules_profile=TENHOU_HANCHAN,model=asdict(self))
        with Path(path).open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)

    @classmethod
    def load(cls,path):
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        if (data['model_version']!=MODEL_VERSION or data['feature_version']!=FEATURE_VERSION
                or data['feature_names']!=list(FEATURE_NAMES) or data['rules_profile']!=TENHOU_HANCHAN):
            raise ValueError('Incompatible shared rank model')
        model=cls(**data['model']);model.validate();return model
