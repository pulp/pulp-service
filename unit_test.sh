#!/bin/bash

# Dummy script for now until we get closer to production

# Upstream Pulp and plugins have their own tests, so does pulp-operator.git

export ACG_CONFIG="$(pwd)/cdappconfig.json"

mkdir -p $ARTIFACTS_DIR
cat << EOF > $ARTIFACTS_DIR/junit-dummy.xml
<testsuite tests="1">
    <testcase classname="dummy" name="dummytest"/>
</testsuite>
EOF

/bin/true

if [ $? != 0 ]; then
    exit 1
fi
