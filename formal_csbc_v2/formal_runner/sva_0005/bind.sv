module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire adv_dvalid,
    input wire Owner,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (adv_dvalid[Owner] == 1'b1));

endmodule


bind keymgr sva_checker sva_checker_inst (.*);
